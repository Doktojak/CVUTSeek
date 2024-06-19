import tkinter as tk
from tkinter import messagebox,ttk
import tkinter.font as font
from threading import Condition
from PIL import Image, ImageTk, ImageDraw,ImageGrab
from time import sleep
import cv2, numpy as np, datetime,os,csv,platform,typek
from seekcamera import (
    SeekCameraIOType,
    SeekCameraColorPalette,
    SeekCameraManager,
    SeekCameraManagerEvent,
    SeekCameraFrameFormat,
    SeekCameraShutterMode,
    SeekCamera,
    SeekFrame,
    SeekCameraFrameHeader,
    SeekCameraTemperatureUnit,
)
CVUTMODRA = "#0091ff"#0065bd"
def check_platform():
    if platform.system() == "Windows":
        return "Windows"
    elif platform.system() == "Linux":
        if "aarch64" in platform.machine():
            return "RPI4"
        else:
            return "Linux"
    else:
        return "Unsupported"
    
PlatCheck = True
try:
    import spidev
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        if check_platform() != "RPI4":
            PlatCheck=False
    except ImportError:
        print("Package RPi.GPIO not installed or program not run on RPI")
        PlatCheck=False
except ImportError:
        print("Package spidev not installed or program not run on RPI")
        PlatCheck=False

class Ads1247:
    
    def __init__(self):
        self.NBITS=24
        self.VREF=2048#2048 mV max amplitude of reference Voltage
        self.PGA=32#V/V gain
        self.LSB=(2*self.VREF)/(self.PGA*(2**(self.NBITS-1))) #LSB of thermocouple input
        self.REFLSB=(2*self.VREF)/(1*(2**(self.NBITS-1))) #LSB of reference temperature diode
        self.TAMB=25 #°C
        self.AMB=118 #mv coresponds to 25 °C
        self.AMBCOEF=0.405 #mV/°C
        self.SELFOCAL=b'\x62'
        self.RESET=b'\x06'
        self.SDATAC=b'\x16'
        self.NOP=b'\xFF'
        self.RDATA=b'\x12'
        self.SYNC=b'\x04\x04'
        self.DRDY=22#GPIO 22
        self.RESETSIG=17#GPIO 17
        self.START=27#GPIO 27

class Renderer:
    """Contains camera and image data required to render images to the screen."""

    def __init__(self):
        self.busy = False
        self.frame = SeekFrame()
        self.camera = SeekCamera()
        self.frame_condition = Condition()
        self.first_frame = True
        self.frame_float= SeekFrame()

class CameraApp:
    def __init__(self, master):
        self.master = master
        master.title("CVUT Doktojak Seek GUI")
        master.attributes("-fullscreen",True)
        if check_platform() == "Windows":
            master.iconbitmap(os.path.expanduser("~/Desktop/Thermography/logo/CVUTlev.ico"))
        else: 
            icon=tk.PhotoImage(file=os.path.expanduser("~/Desktop/Thermography/logo/CVUTlev.png"))
            master.iconphoto(True,icon)

        master.config(bg=CVUTMODRA)
        
        self.default_font = font.nametofont("TkDefaultFont")
        self.height=480
        self.width=800
        self.SS=True #if true the app is being rendered on main screen if not then the app is being rendered on second screen
        self.master.geometry("{}x{}".format(self.width,self.height))
        self.colormap=cv2.COLORMAP_MAGMA
        self.unit=0 # 0 -> °C, 1 -> °F, 2 -> K
        self.emiss=0.97
        self.therm_offest=0
        self.PalIndex=13
        self.ScaleMin=40.0
        self.ScaleMax=50.0
        self.TC_on_off=False
        self.Autoscale=True
        self.capture=False
        self.CSVFlag=False
        self.VIDFlag=False
        self.last_index_pic = self.find_last_index("pic")
        self.last_index_csv = self.find_last_index("csv")
        self.last_index_vid = self.find_last_index("video")
        self.Frames=[]
        #Thermocouple setup
        self.ADS=Ads1247()
        if PlatCheck:
            GPIO.setup(self.ADS.RESETSIG,direction=GPIO.OUT,initial=1)
            GPIO.setup(self.ADS.DRDY,direction=GPIO.IN)
            GPIO.setup(self.ADS.START,direction=GPIO.OUT)
            self.Ktype=spidev.SpiDev()
            self.Ktype.open(0,0)
            self.Ktype.max_speed_hz=4000000
            self.Ktype.mode=0
            GPIO.output(self.ADS.START,GPIO.HIGH)
            self.Ktype.xfer(list(self.ADS.RESET))
            sleep(0.1)
            self.Ktype.xfer(list(self.ADS.SDATAC))
            self.Ktype.xfer(list(b'\x41\x02\x00\x30\x56'))#write to registers from 0x40 to 0x43
            self.Ktype.xfer(list(self.ADS.SELFOCAL))#self calibration of inputs
            sleep(0.1)
            self.Ktype.xfer(list(self.ADS.SYNC))

        
        #toolbar---------------------------------------------------------------------
        self.toolbar = tk.Frame(master, height=30,bg=CVUTMODRA)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)

        self.connect_button = tk.Button(self.toolbar, text="Quit", command=self.Quit)
        self.connect_button.pack(side=tk.RIGHT)
        
        self.Settings = tk.Button(self.toolbar, text="Settings", command=self.create_toolbar_setings)
        self.Settings.pack(side=tk.RIGHT)

        self.create_toolbar_palette()

        self.units_button = tk.Button(self.toolbar, text="Units °C", command=self.Units)
        self.units_button.pack(side=tk.LEFT)

        self.CaptureButton=tk.Button(self.toolbar,text="Capture",command=self.CaptureFrame)
        self.CaptureButton.pack(side=tk.LEFT)

        self.CSVButton=tk.Button(self.toolbar,text="Save CSV",command=self.SaveSCV)
        self.CSVButton.pack(side=tk.LEFT)

        self.VIDButton=tk.Button(self.toolbar,text="Record",command=self.RecordVid)
        self.VIDButton.pack(side=tk.LEFT)


        #camera-------------------------------------------------------------------
        self.camera_manager = SeekCameraManager(SeekCameraIOType.USB)
        self.renderer=Renderer()
        self.camera_connected = False
        try:
            self.camera_manager.register_event_callback(self.on_event,self.renderer)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect to camera: {str(e)}")
        else:
            self.camera_connected = True

        self.image_label = tk.Label(master,bg=CVUTMODRA)
        self.image_label.pack(side=tk.LEFT, anchor=tk.NW)
        #Scale----------------------------------------------------------------------
        self.temperature_scale_canvas=tk.Canvas(master,width=int(self.width*0.0625), height=int(self.height-50),bg=CVUTMODRA,bd=0)
        self.temperature_scale_canvas.pack(side=tk.LEFT,anchor=tk.CENTER)
        # Add text label at the top
        self.top_label = tk.Label(self.temperature_scale_canvas, text="TOP", fg="white",bg=CVUTMODRA,font=(self.default_font.actual()['family'],12,"bold"))
        self.top_label.pack()
        # Create a label to display the image
        self.tempScale = tk.Label(self.temperature_scale_canvas,bg=CVUTMODRA,bd=0)
        self.tempScale.pack() 

        # Add text label at the bottom
        self.bottom_label = tk.Label(self.temperature_scale_canvas, text="BOT", fg="white",bg=CVUTMODRA,font=(self.default_font.actual()['family'],12,"bold"))
        self.bottom_label.pack()

        self.Right_canvas=tk.Canvas(master,bg=CVUTMODRA,bd=0)
        self.Right_canvas.pack(side=tk.LEFT)
        #contact TC--------------------------------------------------------------------
        self.TC_Canvas=tk.Canvas(self.Right_canvas,width=int(self.height*0.15),height=int(self.width*0.02),border=2,bg=CVUTMODRA)
        self.TC_Canvas.pack(side=tk.TOP)
        self.TC_check=tk.Button(self.TC_Canvas,text="Connect TC",command=self.TC_connect,font=(self.default_font.actual()['family'],11))
        self.TC_meas=tk.Label(self.TC_Canvas,text="Measured:",fg="white",bg=CVUTMODRA,font=(self.default_font.actual()['family'],11))
        self.TC_measNum=tk.Label(self.TC_Canvas,text=" ",fg="white",font=(self.default_font.actual()['family'],12),bg=CVUTMODRA)
        self.TC_check.pack(side=tk.TOP)
        #self.TC_meas.pack(side=tk.TOP)

        #Slider
        self.Slider_canvas=tk.Canvas(self.Right_canvas,width=int(self.width*0.05),height=int(self.height*0.85),bg=CVUTMODRA)
        self.Slider_canvas.pack(anchor=tk.CENTER)
        self.create_slider()

        self.CVUTLogo=Image.open(os.path.expanduser("~/Desktop/Thermography/logo/CVUTlevW.png"))
        #self.CVUTLogo = self.CVUTLogo.resize((self.height,self.height), Image.BICUBIC)
        self.CVUTLogoimg = ImageTk.PhotoImage(self.CVUTLogo)
        self.CVUTLabel=tk.Label(self.Right_canvas,image=self.CVUTLogoimg,bg=CVUTMODRA)
        self.CVUTLabel.pack()
        
        self.FELLogo=Image.open(os.path.expanduser("~/Desktop/Thermography/logo/FEL.png"))
        width, height = self.FELLogo.size
        self.FELLogo = self.FELLogo.resize((int(width*0.9),int(height*0.9)), Image.BICUBIC)
        self.FELLogo = ImageTk.PhotoImage(self.FELLogo)
        self.FELLabel=tk.Label(self.Right_canvas,image=self.FELLogo,bg=CVUTMODRA)
        self.FELLabel.pack(side=tk.BOTTOM)


        self.update_image()

    def Quit(self):
        self.camera_connected = False
        self.on_event(self.renderer.camera,SeekCameraManagerEvent.DISCONNECT,0,self.renderer)
        if PlatCheck:
            GPIO.output(self.ADS.START,GPIO.LOW)
            GPIO.cleanup()
            self.Ktype.close()

        self.master.quit()

    def __guiToggle(self):
        print("toggling fullscreen...")
        # Check if the window is currently in fullscreen mode
        if self.master.attributes('-fullscreen'):
            self.master.attributes('-fullscreen', False)
        else:
            self.master.attributes('-fullscreen', True)
        self.settings.destroy()


    def toggle_visibility(self,widget):
        if widget.winfo_ismapped():
            widget.pack_forget()  # Hide the widget
        else:
            widget.pack()  # Show the widget at position (50, 50)


    def find_last_index(self,type):
        filepath="~/Desktop/Thermography"
        if type == "pic":
            filetype = ".png"
            filepath+="/pictures"
        elif type == "csv":
            filetype = ".csv"
            filepath+="/RawData"
        elif type == "video":
            filetype = ".avi"
            filepath+="/videos"
        else:
            return "wrong type"
        directory = os.path.join(os.path.expanduser(filepath))
        if not os.path.exists(directory):
            os.makedirs(directory)
            return 0
        files = [f for f in os.listdir(directory) if f.endswith(filetype)]
        if not files:
            return 0
        return max([int(f.split('CvutSeek')[1].split(filetype)[0]) for f in files])

    def CaptureFrame(self):
        self.capture=True

    def SaveSCV(self):
        self.CSVFlag=True

    def RecordVid(self):
        if not self.VIDFlag:
            self.VIDFlag=True
            self.capture=True
            self.VIDButton.config(text="Stop")
        else:
            self.VIDFlag=False
            self.capture=False
            self.VIDButton.config(text="Record")
            try:
                self.last_index_vid+=1
                save_path = os.path.join(os.path.expanduser("~/Desktop/Thermography/videos"), f"CvutSeek{self.last_index_vid}.avi")
                size = self.Frames[0].size
                #print(f"{len(self.Frames)}")
                fourcc = cv2.VideoWriter_fourcc(*"DIVX")
                out = cv2.VideoWriter(save_path, fourcc, 17.8, size)
                for frame in self.Frames:
                    frame = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
                    out.write(frame)
                out.release()
                self.Frames=[]
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save video: {str(e)}")


    def on_frame(self,_camera, camera_frame, renderer):
        """Async callback fired whenever a new frame is available.

        Parameters
        ----------
        _camera: SeekCamera
            Reference to the camera for which the new frame is available.
        camera_frame: SeekCameraFrame
            Reference to the class encapsulating the new frame (potentially
            in multiple formats).
        renderer: Renderer
            User defined data passed to the callback. This can be anything
            but in this case it is a reference to the renderer object.
        """

        # Acquire the condition variable and notify the main thread
        # that a new frame is ready to render. This is required since
        # all rendering done by OpenCV needs to happen on the main thread.
        with renderer.frame_condition:
            renderer.frame = camera_frame.thermography_float
            renderer.frame_condition.notify()

    def on_event(self, camera, event_type, event_status,renderer):
        if event_type == SeekCameraManagerEvent.CONNECT:
            renderer.busy = True
            renderer.camera = camera

            # Indicate the first frame has not come in yet.
            # This is required to properly resize the rendering window.
            renderer.first_frame = True
            camera.color_palette = SeekCameraColorPalette.TYRIAN

            # Start imaging and provide a custom callback to be called
            # every time a new frame is received.
            camera.register_frame_available_callback(self.on_frame, renderer)
            camera.capture_session_start(SeekCameraFrameFormat.THERMOGRAPHY_FLOAT)
            print("Success", "Camera connected successfully!")
        elif event_type == SeekCameraManagerEvent.DISCONNECT:
            if renderer.camera == camera:
                # Stop imaging and reset all the renderer state.
                camera.capture_session_stop()
                renderer.camera = None
                renderer.frame = None
                renderer.busy = False
            print("Disconnected", "Camera disconnected.")

    def min_change(self,num,label):
        tmp = self.ScaleMin + num
        if (tmp > self.ScaleMax):
            tmp = self.ScaleMax
            
        self.ScaleMin=tmp
        label.config(text=f"Min: {round(self.ScaleMin,2)}")

    def max_change(self,num,label):
        tmp = self.ScaleMax + num
        if (tmp < self.ScaleMin):
            tmp = self.ScaleMin
            
        self.ScaleMax=tmp
        label.config(text=f"Max: {round(self.ScaleMax,2)}")


    def ScaleMinMax_win(self):
        self.settings.destroy() 
        minmax = tk.Toplevel(self.master)
        minmax.title("Set min and max of Temp Scale")
        minmax.geometry("+200+200") 
                
        min_label = tk.Label(minmax, text=f"Min: {round(self.ScaleMin,2)}",fg="black",font=(self.default_font.actual()['family'],11,"bold")) 
        minButt=tk.Button(minmax,text="+0.1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=0.1: self.min_change(num,min_label))
        minButt2=tk.Button(minmax,text="+1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=1: self.min_change(num,min_label))
        minButt3=tk.Button(minmax,text="+10",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=10: self.min_change(num,min_label))
        minButt4=tk.Button(minmax,text="-0.1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-0.1: self.min_change(num,min_label))
        minButt5=tk.Button(minmax,text="-1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-1: self.min_change(num,min_label))
        minButt6=tk.Button(minmax,text="-10",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-10: self.min_change(num,min_label))
        
        minButt.grid(row=0, column=0, padx=5, pady=5)
        minButt2.grid(row=0, column=1, padx=5, pady=5)
        minButt3.grid(row=0, column=2, padx=5, pady=5)
        min_label.grid(row=1, column=0, columnspan=3)
        minButt4.grid(row=2, column=0, padx=5, pady=5)
        minButt5.grid(row=2, column=1, padx=5, pady=5)
        minButt6.grid(row=2, column=2, padx=5, pady=5)

        max_label = tk.Label(minmax, text=f"Max: {round(self.ScaleMax,2)}",fg="black",font=(self.default_font.actual()['family'],11,"bold"))
        maxButt=tk.Button(minmax,text="+0.1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=0.1: self.max_change(num,max_label))
        maxButt2=tk.Button(minmax,text="+1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=1: self.max_change(num,max_label))
        maxButt3=tk.Button(minmax,text="+10",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=10: self.max_change(num,max_label))
        maxButt4=tk.Button(minmax,text="-0.1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-0.1: self.max_change(num,max_label))
        maxButt5=tk.Button(minmax,text="-1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-1: self.max_change(num,max_label))
        maxButt6=tk.Button(minmax,text="-10",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-10: self.max_change(num,max_label))
        
        maxButt.grid(row=0, column=3, padx=5, pady=5)
        maxButt2.grid(row=0, column=4, padx=5, pady=5)
        maxButt3.grid(row=0, column=5, padx=5, pady=5)
        max_label.grid(row=1, column=3, columnspan=3)
        maxButt4.grid(row=2, column=3, padx=5, pady=5)
        maxButt5.grid(row=2, column=4, padx=5, pady=5)
        maxButt6.grid(row=2, column=5, padx=5, pady=5)

        done= tk.Button(minmax,text="Done",command=minmax.destroy)
        done.grid(row=0,column=6,rowspan=3)



    def Autoscale_toggle(self):
        self.Autoscale = not self.Autoscale
        if self.Autoscale == False:
            self.ScaleMinMax_win()
        self.settings.destroy() 
        
    def ScreenToggle(self):
        self.SS=not self.SS
        self.settings.destroy()

    def create_toolbar_setings(self):
        # Create a popup window
        self.settings = tk.Toplevel(self.master)   
        self.settings.title("Settings")
        self.settings.geometry("+250+250") 
        
        # Create a frame to hold the buttons
        button_frame = tk.Frame(self.settings)
        button_frame.pack()
        
        buttonGUIReset = tk.Button(button_frame,text="Toggle Fullscreen", command=self.__guiToggle)
        buttonGUIReset.grid(row=0, column=0, padx=5, pady=5)            

        if self.Autoscale:
            AscText = "ON"
        else:
            AscText="OFF"

        AScale = tk.Button(button_frame,text=f"Autoscale: {AscText}", command=self.Autoscale_toggle)
        AScale.grid(row=0, column=1, padx=5, pady=5)  

        Screen = tk.Button(button_frame,text="SreenSwitch",command=self.ScreenToggle)
        Screen.grid(row=1,column=0,padx=5,pady=5)

        if not self.Autoscale:
            Scale=tk.Button(button_frame,text="Scale setting",command=self.ScaleMinMax_win)  
            Scale.grid(row=1, column=1, padx=5, pady=5)       


    def create_toolbar_palette(self):
        self.options = ["Autumn","Bone","Jet",
                   "Winter","Rainbow","Ocean",
                   "Summer","Spring","Cool",
                   "HSV","Pink","Hot",
                   "Parula","Magma","Inferno",
                    "Plasma","Viridis","Cividis",
                    "Twilight","TwilightS","Turbo",
                    "DeepG"]
        self.Palette=tk.Button(self.toolbar, text=f"Palette: {self.options[13]}", command=self.PalettePopup)
        self.Palette.pack(side=tk.LEFT)

    
    def PalettePopup(self):
        # Create a popup window
        self.popup = tk.Toplevel(self.master)   
        self.popup.title("Choose a color palette")
        self.popup.geometry("+20+20")
        if check_platform() == "Windows":
            self.popup.iconbitmap(os.path.expanduser("~/Desktop/Thermography/logo/Palette.ico"))
        else:
            icon=tk.PhotoImage(file=os.path.expanduser("~/Desktop/Thermography/logo/Palette.png"))
            self.popup.iconphoto(True,icon)
        
        # Create a frame to hold the buttons
        self.button_frame = tk.Frame(self.popup)
        self.button_frame.pack()
        CVColor = [cv2.COLORMAP_AUTUMN,cv2.COLORMAP_BONE,cv2.COLORMAP_JET,
                    cv2.COLORMAP_WINTER,cv2.COLORMAP_RAINBOW,cv2.COLORMAP_OCEAN,
                    cv2.COLORMAP_SUMMER,cv2.COLORMAP_SPRING,cv2.COLORMAP_COOL,
                    cv2.COLORMAP_HSV,cv2.COLORMAP_PINK,cv2.COLORMAP_HOT,
                    cv2.COLORMAP_PARULA,cv2.COLORMAP_MAGMA,cv2.COLORMAP_INFERNO,
                    cv2.COLORMAP_PLASMA,cv2.COLORMAP_VIRIDIS,cv2.COLORMAP_CIVIDIS,
                    cv2.COLORMAP_TWILIGHT,cv2.COLORMAP_TWILIGHT_SHIFTED,cv2.COLORMAP_TURBO,
                    cv2.COLORMAP_DEEPGREEN]

        # Create buttons and add them to the frame
        
        values = np.linspace(0, 1, 256)

        # Scale the values to the range [0, 255]
        scaled_values = (values * 255).astype(np.uint8)
        # Flip the transposed image both vertically and horizontally
        #color_map_image_flipped = cv2.flip(color_map_image, -1)

        for i in range(22):
            button = tk.Button(self.button_frame, command=lambda num=i: self.get_selected_palette(num))
            button.grid(row=i // 5, column=i % 5, padx=5, pady=5)            
            img=cv2.applyColorMap(np.expand_dims(scaled_values, axis=0), CVColor[i])
            img=cv2.resize(img, (120, 50))
            img=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img=ImageTk.PhotoImage(Image.fromarray(img))
            button.imtk=img
            button.config(image=img)
        

    def get_selected_palette(self,num):
        self.PalIndex=num
        self.popup.destroy()
        selected_option=self.options[self.PalIndex]
        self.Palette.config(text=f"Palette: {self.options[self.PalIndex]}")
        CVColor = [cv2.COLORMAP_AUTUMN,cv2.COLORMAP_BONE,cv2.COLORMAP_JET,
                    cv2.COLORMAP_WINTER,cv2.COLORMAP_RAINBOW,cv2.COLORMAP_OCEAN,
                    cv2.COLORMAP_SUMMER,cv2.COLORMAP_SPRING,cv2.COLORMAP_COOL,
                    cv2.COLORMAP_HSV,cv2.COLORMAP_PINK,cv2.COLORMAP_HOT,
                    cv2.COLORMAP_PARULA,cv2.COLORMAP_MAGMA,cv2.COLORMAP_INFERNO,
                    cv2.COLORMAP_PLASMA,cv2.COLORMAP_VIRIDIS,cv2.COLORMAP_CIVIDIS,
                    cv2.COLORMAP_TWILIGHT,cv2.COLORMAP_TWILIGHT_SHIFTED,cv2.COLORMAP_TURBO,
                    cv2.COLORMAP_DEEPGREEN]
        if selected_option == "Autumn":
            self.colormap=CVColor[0]
        elif selected_option == "Bone":
            self.colormap=CVColor[1]
        elif selected_option == "Jet":
            self.colormap=CVColor[2]
        elif selected_option == "Winter":
            self.colormap= CVColor[3]
        elif selected_option == "Rainbow":
            self.colormap= CVColor[4]
        elif selected_option == "Ocean":
            self.colormap= CVColor[5]
        elif selected_option == "Summer":
            self.colormap= CVColor[6]
        elif selected_option == "Spring":
            self.colormap= CVColor[7]
        elif selected_option == "Cool":
            self.colormap= CVColor[8]
        elif selected_option == "HSV":
            self.colormap= CVColor[9]
        elif selected_option == "Pink":
            self.colormap= CVColor[10]
        elif selected_option == "Hot":
            self.colormap= CVColor[11]
        elif selected_option == "Parula":
            self.colormap= CVColor[12]
        elif selected_option == "Magma":
            self.colormap= CVColor[13]
        elif selected_option == "Inferno":
            self.colormap= CVColor[14]
        elif selected_option == "Plasma":
            self.colormap= CVColor[15]
        elif selected_option == "Viridis":
            self.colormap= CVColor[16]
        elif selected_option == "Cividis":
            self.colormap= CVColor[17]
        elif selected_option == "Twilight":
            self.colormap= CVColor[18]
        elif selected_option == "TwilightS":
            self.colormap= CVColor[19]
        elif selected_option == "Turbo":
            self.colormap= CVColor[20]
        elif selected_option == "DeepG":
            self.colormap= CVColor[21]
        
    def Units(self):
        self.unit=(self.unit+1)%3
        self.renderer.camera.temperature_unit=SeekCameraTemperatureUnit(self.unit)   
        if self.unit == 0:
            Tunit = "°C"
        elif self.unit == 1:
            Tunit = "°F"
        elif self.unit == 2:
            Tunit="K"
        self.units_button.config(text=f"Units: {Tunit}")


    def normalize(self,frame):
        if not self.Autoscale:
            frame=np.clip(frame,self.ScaleMin,self.ScaleMax)
        return ((frame - np.min(frame)) / (np.max(frame) - np.min(frame))*255).astype(np.uint8)
        
    def update_image(self):
        if self.renderer.frame_condition and not self.renderer.first_frame:
            try:
                frame = self.renderer.frame
                #print(cv2.applyColorMap(self.normalize(frame.data), self.colormap))
                if frame is not None:
                    if self.CSVFlag:
                        self.CSVFlag=False
                        self.last_index_csv+=1
                        filename=f"CvutSeek{self.last_index_csv}.csv"
                        with open(os.path.join(os.path.expanduser("~/Desktop/Thermography/RawData"), filename), "w", newline="") as csvfile:
                            writer = csv.writer(csvfile)
                            for row in frame.data:
                                writer.writerow(row)
                    img = Image.fromarray(cv2.cvtColor(cv2.applyColorMap(self.normalize(frame.data), self.colormap), cv2.COLOR_BGR2RGB))
                    #img=img.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.FLIP_LEFT_RIGHT)
                 
                    self.height=self.master.winfo_height()
                    self.width=self.master.winfo_width()
                    height=int(self.height-40)
                    if self.SS:
                        width=int(4*height/3)-10
                    else:
                        width=self.width - 225
                    img = img.resize((int(width/2) ,int(height/2 )),Image.Resampling.LANCZOS) 
                    img = img.resize((width ,height ),Image.Resampling.LANCZOS)  # Resize the image
                    img = ImageTk.PhotoImage(img)
                    self.image_label.imgtk = img
                    self.image_label.config(image=img)

                    
                    #ADD update TC temp
                    if self.TC_on_off:  
                        if self.counter == 10:
                            self.TC_measure()
                            self.counter=0
                        self.counter+=1
                    # Update temperature scale canvas
                    self.update_TempLabel()
                    if self.capture:
                        x = self.image_label.winfo_rootx()+5
                        y = self.image_label.winfo_rooty()+5
                        x1 = x + self.image_label.winfo_width() +self.temperature_scale_canvas.winfo_width()
                        y1 = y + self.image_label.winfo_height()-5
                        #print(f"x: {x},y: {y},x1: {x1},y1: {y1}")
                        IMGCapture=ImageGrab.grab().crop((x, y, x1, y1))
                        if self.VIDFlag:
                            self.Frames.append(IMGCapture)
                        else:
                            self.last_index_pic += 1
                            IMGCapture.save(os.path.join(os.path.expanduser("~/Desktop/Thermography/pictures"), f"CvutSeek{self.last_index_pic}.png"))
                            self.capture=False

            except Exception as e:
                print("Error", f"Failed to get camera image: {str(e)}")
        else:
            sleep(1)
            self.renderer.first_frame=False

        self.master.after(1, self.update_image)

    def TC_connect(self):
        if not self.TC_on_off:
            self.TC_on_off = True
            self.counter=0
        elif self.TC_on_off:
            self.TC_on_off=False
        self.toggle_visibility(self.TC_meas)
        self.toggle_visibility(self.TC_measNum)

    
    def TC_read(self):
        self.Ktype.xfer2(list(b'\x42\x00\x30'))#switch inputs
        sleep(0.02)
        self.Ktype.xfer2(list(self.ADS.RDATA))
        bIn=self.Ktype.readbytes(3)
        print("input= {}",bIn)
        ktc=int.from_bytes(bIn, byteorder='big', signed=True)
        if ktc > 54/self.ADS.LSB:
            ktc-=2**23
        #ktc=hex2dec(bIn)
        print("tmpK= {}",ktc)
        ktc=ktc*self.ADS.LSB
        #ktc=((ktc/((2**23)-1))-0.5)*ADS.VREF/ADS.PGA
        #print("ktcmV=",ktc)
        return ktc

    def AMB_read(self):
        self.Ktype.xfer2(list(b'\x42\x00\x33'))#switch inputs to internal ambient temerature reading
        sleep(0.01)
        self.Ktype.xfer2(list(self.ADS.RDATA))
        ambient=int.from_bytes(self.Ktype.readbytes(3), byteorder='big', signed=True)
        #print("DecA= {}",ambient)
        ambient=ambient*self.ADS.REFLSB
        #print("ambientDmV=",ambient)
        ambient=(ambient-self.ADS.AMB)/self.ADS.AMBCOEF
        ambient+=self.ADS.TAMB
        return ambient

    def TC_measure(self):
        if not PlatCheck:
            self.TC_measNum.config(text="no RPI")
        else:
            TC_out="not ready"
            sleep(0.01)
            ktc=round(self.TC_read(),2)#generated voltage of thermopile in mV
            amb=int(self.AMB_read())
            print(ktc)
            print(amb)
            if (ktc < 54) and (ktc > -6) and (amb< 255) and (amb>-266):  
                TC_val=typek.get_temp(amb,ktc)
                if self.unit == 0:
                    TC_out = str("{:.2f} °C").format(TC_val)
                elif self.unit == 1:
                    TC_val=TC_val*9/5 + 32
                    TC_out = str("{:.2f} °F").format(TC_val)
                elif self.unit == 2:
                    TC_val=TC_val+273.15
                    TC_out = str("{:.2f} K").format(TC_val)
            else:
                TC_out="TC open"

            self.TC_measNum.config(text=TC_out)


    def update_TempLabel(self):
        # Define temperature range and corresponding colors
        if 1:
            if self.Autoscale:
                self.ScaleMin = min_temperature = self.renderer.frame.header.thermography_min[2]
                self.ScaleMax = max_temperature = self.renderer.frame.header.thermography_max[2]
            else:
                min_temperature = self.ScaleMin
                max_temperature = self.ScaleMax
        else:
            min_temperature = self.renderer.frame.header.thermography_min[2]
            max_temperature = self.renderer.frame.header.thermography_max[2]
        
        if self.unit == 0:
            Tunit = "°C"
        elif self.unit == 1:
            Tunit = "°F"
        elif self.unit == 2:
            Tunit="K"

        self.top_label.configure(text=str("{:.1f} {}").format(max_temperature,Tunit))

        self.bottom_label.configure(text=str("{:.1f} {}").format(min_temperature,Tunit))
        tempscale=self.create_temperature_scale_image()
        self.tempScale.imtk=tempscale
        self.tempScale.config(image=tempscale)


    def create_temperature_scale_image(self):
        values = np.linspace(0, 1, 256)

        # Scale the values to the range [0, 255]
        scaled_values = (values * 255).astype(np.uint8)

        # Create a single-row image with the colormap applied
        color_map_image = cv2.applyColorMap(np.expand_dims(scaled_values, axis=0), self.colormap)

        # Transpose the image to change the orientation of colors
        color_map_image_transposed = cv2.transpose(color_map_image)

        # Flip the transposed image both vertically and horizontally
        color_map_image_flipped = cv2.flip(color_map_image_transposed, -1)

        # Resize the image to be 30 pixels wide
        resized_image = cv2.resize(color_map_image_flipped, (int(self.width*0.03), int(self.image_label.winfo_height()*0.85)))

        # Convert image to RGB format
        color_map_image_rgb = cv2.cvtColor(resized_image, cv2.COLOR_BGR2RGB)

        # Convert image to Tkinter PhotoImage
        return ImageTk.PhotoImage(Image.fromarray(color_map_image_rgb))
    
    def create_slider(self):
        self.emsAdd1=tk.Button(self.Slider_canvas,text="+0.01",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=0.01: self.slider_changed(num))
        self.emsAdd2=tk.Button(self.Slider_canvas,text="+0.1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=0.1: self.slider_changed(num))
        self.ems_label = tk.Label(self.Slider_canvas, text=f"ε: {self.emiss}",fg="white",font=(self.default_font.actual()['family'],11,"bold"),bg=CVUTMODRA) #{}").format(slider_value))
        self.emsSub1=tk.Button(self.Slider_canvas,text="-0.01",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-0.01: self.slider_changed(num))
        self.emsSub2=tk.Button(self.Slider_canvas,text="-0.1",fg="black",font=(self.default_font.actual()['family'],11),command=lambda num=-0.1: self.slider_changed(num))
        
        self.emsAdd1.grid(row=0, column=0, padx=5, pady=5)
        self.emsAdd2.grid(row=0, column=1, padx=5, pady=5)
        self.ems_label.grid(row=1, column=0, columnspan=2)
        self.emsSub1.grid(row=2, column=0, padx=5, pady=5)
        self.emsSub2.grid(row=2, column=1, padx=5, pady=5)
    
    def slider_changed(self, value):
        self.emiss=round(((self.emiss+value)%1.0),2)
        if (self.emiss==0.00):
            self.emiss=0.01
        self.ems_label.config(text=f"ε: {self.emiss}")
        self.renderer.camera.scene_emissivity=round(self.emiss,2)
        #self.slider_label.config(text=str("Emisivity: {}").format(value))



def main():
    root = tk.Tk()
    app = CameraApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
