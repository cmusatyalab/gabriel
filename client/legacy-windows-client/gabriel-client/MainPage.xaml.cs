using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO.Compression;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using Windows.ApplicationModel;
using Windows.ApplicationModel.Core;
using Windows.Data.Json;
using Windows.Devices.Enumeration;
using Windows.Foundation;
using Windows.Foundation.Collections;
using Windows.Graphics.Display;
using Windows.Graphics.Imaging;
using Windows.Media;
using Windows.Media.Capture;
using Windows.Media.MediaProperties;
using Windows.Media.SpeechSynthesis;
using Windows.Networking;
using Windows.Networking.Sockets;
using Windows.Security.Cryptography;
using Windows.Storage;
using Windows.Storage.Streams;
using Windows.System.Display;
using Windows.UI.Core;
using Windows.UI.Xaml;
using Windows.UI.Xaml.Controls;
using Windows.UI.Xaml.Input;
using Windows.UI.Xaml.Media.Imaging;
using Windows.UI.Xaml.Navigation;

namespace gabriel_client
{
    [ComImport]
    [Guid("5b0d3235-4dba-4d44-865e-8f1d0e4fd04d")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    unsafe interface IMemoryBufferByteAccess
    {
        void GetBuffer(out byte* buffer, out uint capacity);
    }
    
    public sealed partial class MainPage : Page
    {
        // Prevent the screen from sleeping while the camera is running
        private readonly DisplayRequest _displayRequest = new DisplayRequest();

        // For listening to media property changes
        private readonly SystemMediaTransportControls _systemMediaControls = SystemMediaTransportControls.GetForCurrentView();

        // MediaCapture and its state variables
        private MediaCapture _mediaCapture;
        private bool _isInitialized = false;
        private bool _isPreviewing = false;

        // Information about the camera device
        private bool _mirroringPreview = false;

        // Timer to process (transmit) preview frame periodically
        private DispatcherTimer _getFrameTimer;

        // State variable to make sure no timer tasks are overlapped
        private bool _isDoingTimerTask = false;
        private Object _timerTaskLock = new Object();
        private bool _isDoingPingTask = false;
        private Object _timerPingLock = new Object();

        // Networking components
        private StreamSocket _controlSocket;
        private StreamSocket _videoStreamSocket;
        private StreamSocket _resultReceivingSocket;
        private DataReader _controlSocketReader;
        private DataReader _videoStreamSocketReader;
        private DataReader _resultReceivingSocketReader;
        private DataWriter _controlSocketWriter;
        private DataWriter _videoStreamSocketWriter;
        private DataWriter _resultReceivingSocketWriter;

        private IAsyncAction _resultReceivingWorkItem;

        // File operations
        private IReadOnlyList<StorageFile> _imageFiles;
        private int _indexImageFile;
        private IReadOnlyList<StorageFile> _imageFilesCompress;
        private List<SoftwareBitmap> _imageBitmapsCompress;
        private int _indexImageFileCompress;
        private int _imageFileCompressLength;

        // Token control
        private long _frameID = 0;
        private Object _frameIdLock = new Object();
        private TokenController _tokenController;

        // Feedback
        MediaElement _mediaElement;

        // Logger
        private MyLogger _myLogger;

        // Timer to stop program when doing experiments
        private DispatcherTimer _stopTimer;

        // Fake media control
        private CaptureElement PreviewControlFake;


        #region Constructor, lifecycle and navigation

        public MainPage()
        {
            this.InitializeComponent();

            // Cache the UI to have the checkboxes retain their state, as the enabled/disabled state of the
            // GetPreviewFrameButton is reset in code when suspending/navigating (see Start/StopPreviewAsync)
            //NavigationCacheMode = NavigationCacheMode.Required;

            // Useful to know when to initialize/clean up the camera
            Application.Current.Suspending += Application_Suspending;
            Application.Current.Resuming += Application_Resuming;
            
            // Initialize timer for frame capture
            _getFrameTimer = new DispatcherTimer();
            _getFrameTimer.Tick += _getFrameTimer_Tick;
            _getFrameTimer.Interval = new TimeSpan(0, 0, 0, 0, 67); // 15 fps

            // Initialize logger
            _myLogger = new MyLogger("latency-" + Const.SERVER_IP + "-" + Const.TOKEN_SIZE + ".txt");

            // Initialize token control
            _tokenController = new TokenController(Const.TOKEN_SIZE, _myLogger);

            // Prepare TTS
            _mediaElement = new MediaElement();

            // Timer to stop program in exp mode
            if (Const.IS_EXPERIMENT)
            {
                _stopTimer = new DispatcherTimer();
                _stopTimer.Tick += _stopTimer_Tick;
                _stopTimer.Interval = new TimeSpan(0, 0, 3, 0, 0); // 5 mins
            }
        }

        private async void Application_Suspending(object sender, SuspendingEventArgs e)
        {
            Debug.WriteLine("++Suspending");
            // Handle global application events only if this page is active
            if (Frame.CurrentSourcePageType == typeof(MainPage))
            {
                var deferral = e.SuspendingOperation.GetDeferral();

                await CleanupCameraAsync();
                
                deferral.Complete();
            }
        }

        private async void Application_Resuming(object sender, object o)
        {
            Debug.WriteLine("++Resuming");
            // Handle global application events only if this page is active
            if (Frame.CurrentSourcePageType == typeof(MainPage))
            {
                Debug.WriteLine("Resuming Called");
                await InitializeCameraAsync();

                _getFrameTimer.Start();
            }

            // Initialize network
            await InitializeNetworkAsync();
            await GetServerTimeOffsetAsync();
        }

        
        protected override async void OnNavigatedTo(NavigationEventArgs e)
        {
            Debug.WriteLine("++OnNavigatedTo");

            // Initialize file loaders
            await InitializeFileLoading();

            // Initialize logger
            await _myLogger.InitializeLogger();

            // Initialize network
            await InitializeNetworkAsync();
            await GetServerTimeOffsetAsync();

            // Initialize camera
            await InitializeCameraAsync();
            _getFrameTimer.Start();

            // Stop timer
            if (Const.IS_EXPERIMENT)
            {
                _stopTimer.Start();
            }
   
        }

        protected override async void OnNavigatingFrom(NavigatingCancelEventArgs e)
        {
            Debug.WriteLine("++OnNavigatedFrom");
            // Handling of this event is included for completenes, as it will only fire when navigating between pages and this sample only includes one page

            await CleanupCameraAsync();
        }

        #endregion Constructor, lifecycle and navigation


        #region Event handlers

        /// <summary>
        /// In the event of the app being minimized this method handles media property change events. If the app receives a mute
        /// notification, it is no longer in the foregroud.
        /// </summary>
        /// <param name="sender"></param>
        /// <param name="args"></param>
        private async void SystemMediaControls_PropertyChanged(SystemMediaTransportControls sender, SystemMediaTransportControlsPropertyChangedEventArgs args)
        {
            await Dispatcher.RunAsync(CoreDispatcherPriority.Normal, async () =>
            {
                // Only handle this event if this page is currently being displayed
                if (args.Property == SystemMediaTransportControlsProperty.SoundLevel && Frame.CurrentSourcePageType == typeof(MainPage))
                {
                    // Check to see if the app is being muted. If so, it is being minimized.
                    // Otherwise if it is not initialized, it is being brought into focus.
                    if (sender.SoundLevel == SoundLevel.Muted)
                    {
                        await CleanupCameraAsync();
                    }
                    else if (!_isInitialized)
                    {
                        await InitializeCameraAsync();
                    }
                }
            });
        }
        
        private async void MediaCapture_Failed(MediaCapture sender, MediaCaptureFailedEventArgs errorEventArgs)
        {
            Debug.WriteLine("MediaCapture_Failed: (0x{0:X}) {1}", errorEventArgs.Code, errorEventArgs.Message);

            await CleanupCameraAsync();
        }

        private async void _getFrameTimer_Tick(object sender, object e)
        {
            //Debug.WriteLine("TimerTick Called");
            lock (_frameIdLock)
            {
                _frameID++;
            }
            Debug.WriteLine("id: " + _frameID);

            //At least ping to make sure the network is still active
            bool tempFlag = false;
            lock (_timerPingLock)
            {
                if (_isDoingPingTask == false)
                {
                    _isDoingPingTask = true;
                    tempFlag = true;
                }
            }
            tempFlag = false;
            if (tempFlag)
            {
                // send a fake time to server
                string jsonData = "{\"sync_time\":" + 1 + "}";
                _controlSocketWriter.WriteInt32(checked((int)_controlSocketWriter.MeasureString(jsonData)));
                _controlSocketWriter.WriteString(jsonData);
                await _controlSocketWriter.StoreAsync();

                // receive current time at server
                string recvMsg = await receiveMsgAsync(_controlSocketReader);
                _isDoingPingTask = false;
            }

            tempFlag = false;
            lock (_timerTaskLock)
            {
                if (_isDoingTimerTask == false)
                {
                    _isDoingTimerTask = true;
                    tempFlag = true;
                }
            }
            if (tempFlag)
            {
                await StreamPreviewFrameAsync(_frameID);
                _isDoingTimerTask = false;
            }
        }

        private async void _stopTimer_Tick(object sender, object e)
        {
            // Stop everything
            Debug.WriteLine("Stopping the program");
            _getFrameTimer.Stop();

            await GetServerTimeOffsetAsync();
            CoreApplication.Exit();
        }

        #endregion Event handlers


        #region File loading methods
        private async Task InitializeFileLoading()
        {
            var myPictures = await StorageLibrary.GetLibraryAsync(KnownLibraryId.Pictures);
            Const.ROOT_DIR = await myPictures.SaveFolder.GetFolderAsync("Camera Roll");

            if (Const.LOAD_IMAGES)
            {
                // check input data at image directory
                _imageFiles = await GetImageFiles(Const.ROOT_DIR, Const.TEST_IMAGE_DIR_NAME);
                if (_imageFiles.LongCount() == 0)
                {
                    Debug.WriteLine("test image directory empty!");
                }
                else
                {
                    Debug.WriteLine("Number of image files in the input folder: " + _imageFiles.LongCount());
                }
                _indexImageFile = 0;
            }

            if (Const.IS_EXPERIMENT)
            {
                _imageFilesCompress = await GetImageFiles(Const.ROOT_DIR, Const.COMPRESS_IMAGE_DIR_NAME);
                _imageBitmapsCompress = new List<SoftwareBitmap>();
                int i = 0;
                foreach (var imageFile in _imageFilesCompress)
                {
                    using (IRandomAccessStream stream = await imageFile.OpenAsync(FileAccessMode.Read))
                    {
                        BitmapDecoder decoder = await BitmapDecoder.CreateAsync(stream);
                        SoftwareBitmap bitmap = await decoder.GetSoftwareBitmapAsync();
                        _imageBitmapsCompress.Add(bitmap);
                    }
                    i++;
                    if (i == Const.MAX_COMPRESS_IMAGE) break;
                }
                _imageFileCompressLength = i;
            }
        }

        #endregion File loading methods


        #region MediaCapture methods

        /// <summary>
        /// Initializes the MediaCapture, registers events, gets camera device information for mirroring and rotating, and starts preview
        /// </summary>
        /// <returns></returns>
        private async Task InitializeCameraAsync()
        {
            Debug.WriteLine("InitializeCameraAsync");

            if (_mediaCapture == null)
            {
                // Attempt to get the back camera if one is available, but use any camera device if not
                var cameraDevice = await FindCameraDeviceByPanelAsync(Windows.Devices.Enumeration.Panel.Back);

                if (cameraDevice == null)
                {
                    Debug.WriteLine("No camera device found!");
                    return;
                }

                // Create MediaCapture and its settings
                _mediaCapture = new MediaCapture();

                // Register for a notification when something goes wrong
                _mediaCapture.Failed += MediaCapture_Failed;

                var mediaInitSettings = new MediaCaptureInitializationSettings { VideoDeviceId = cameraDevice.Id };
                /*IReadOnlyList<MediaCaptureVideoProfile> profiles = MediaCapture.FindAllVideoProfiles(cameraDevice.Id);
                // Debug all possible (capture) resolution
                foreach (var p in profiles)
                {
                    foreach (var d in p.SupportedRecordMediaDescription)
                    {
                        Debug.WriteLine("" + d.Width + ", " + d.Height + ", " + d.FrameRate);
                    }
                    
                }

                var match = (from profile in profiles
                             from desc in profile.SupportedRecordMediaDescription
                             where desc.Width == Const.IMAGE_WIDTH && desc.Height == Const.IMAGE_HEIGHT && Math.Round(desc.FrameRate) >= Const.MIN_FPS
                             select new { profile, desc }).FirstOrDefault();

                if (match != null)
                {
                    mediaInitSettings.VideoProfile = match.profile;
                    mediaInitSettings.RecordMediaDescription = match.desc;
                }
                else
                {
                    Debug.WriteLine("Camera does not support desired resolution or framerate");
                    mediaInitSettings.VideoProfile = profiles[0];
                }*/

                // Initialize MediaCapture
                try
                {
                    await _mediaCapture.InitializeAsync(mediaInitSettings);
                    _isInitialized = true;
                }
                catch (UnauthorizedAccessException)
                {
                    Debug.WriteLine("The app was denied access to the camera");
                }

                // Query all properties of the specified stream type 
                IEnumerable<StreamPropertiesHelper> allStreamProperties =
                    _mediaCapture.VideoDeviceController.GetAvailableMediaStreamProperties(MediaStreamType.VideoPreview).Select(x => new StreamPropertiesHelper(x));
                // Order them by resolution then frame rate
                allStreamProperties = allStreamProperties.OrderBy(x => x.Height * x.Width).ThenBy(x => x.FrameRate);
                //foreach (var streamProperty in allStreamProperties)
                //{
                //    Debug.WriteLine("" + streamProperty.Width + ", " + streamProperty.Height + ", " + streamProperty.FrameRate);
                //}
                var encodingProperties = allStreamProperties.ElementAt(1).EncodingProperties; // TODO: this only works for Hololens
                await _mediaCapture.VideoDeviceController.SetMediaStreamPropertiesAsync(MediaStreamType.VideoPreview, encodingProperties);


                // If initialization succeeded, start the preview
                if (_isInitialized)
                {
                    // Figure out where the camera is located
                    if (cameraDevice.EnclosureLocation != null)
                    {
                        // Only mirror the preview if the camera is on the front panel
                        _mirroringPreview = (cameraDevice.EnclosureLocation.Panel == Windows.Devices.Enumeration.Panel.Front);
                    }

                    await StartPreviewAsync();
                }
            }
        }

        /// <summary>
        /// Starts the preview and adjusts it for for rotation and mirroring after making a request to keep the screen on and unlocks the UI
        /// </summary>
        /// <returns></returns>
        private async Task StartPreviewAsync()
        {
            Debug.WriteLine("StartPreviewAsync");

            // Prevent the device from sleeping while the preview is running
            _displayRequest.RequestActive();

            // Register to listen for media property changes
            _systemMediaControls.PropertyChanged += SystemMediaControls_PropertyChanged;

            // Set the preview source in the UI and mirror it if necessary
            if (Const.DISPLAY_PREVIEW)
            {
                PreviewControl.Source = _mediaCapture;
                PreviewControl.FlowDirection = _mirroringPreview ? FlowDirection.RightToLeft : FlowDirection.LeftToRight;
            }
            else
            {
                PreviewControlFake = new CaptureElement();
                PreviewControlFake.Source = _mediaCapture;
                PreviewControlFake.FlowDirection = _mirroringPreview ? FlowDirection.RightToLeft : FlowDirection.LeftToRight;
            }

            // Start the preview
            await _mediaCapture.StartPreviewAsync();
            _isPreviewing = true;
        }

        /// <summary>
        /// Stops the preview and deactivates a display request, to allow the screen to go into power saving modes, and locks the UI
        /// </summary>
        /// <returns></returns>
        private async Task StopPreviewAsync()
        {
            _isPreviewing = false;
            await _mediaCapture.StopPreviewAsync();

            // Use the dispatcher because this method is sometimes called from non-UI threads
            await Dispatcher.RunAsync(CoreDispatcherPriority.Normal, () =>
            {
                if (Const.DISPLAY_PREVIEW)
                    PreviewControl.Source = null;
                else
                    PreviewControlFake.Source = null;

                // Allow the device to sleep now that the preview is stopped
                _displayRequest.RequestRelease();
            });
        }

        /// <summary>
        /// Gets the current preview frame as a SoftwareBitmap
        /// </summary>
        /// <returns></returns>
        private async Task StreamPreviewFrameAsync(long frameID)
        {
            //            long startTime = GetTimeMillis();
            long dataTime = 0, compressedTime = 0;
            byte[] imageData;
            if (!Const.LOAD_IMAGES)
            {
                // Get information about the preview
                var previewProperties = _mediaCapture.VideoDeviceController.GetMediaStreamProperties(MediaStreamType.VideoPreview) as VideoEncodingProperties;

                // Create the video frame to request a SoftwareBitmap preview frame
                var videoFrame = new VideoFrame(BitmapPixelFormat.Bgra8, (int)previewProperties.Width, (int)previewProperties.Height);

                // Capture the preview frame
                using (var currentFrame = await _mediaCapture.GetPreviewFrameAsync(videoFrame))
                {
                    // Collect the resulting frame
                    SoftwareBitmap frame = currentFrame.SoftwareBitmap;

                    // Compress to JPEG bytes
                    imageData = await Bitmap2JPEG(frame);

                    // Show the frame (as is, no rotation is being applied)
                    // Create a SoftwareBitmapSource to display the SoftwareBitmap to the user
                    //                var sbSource = new SoftwareBitmapSource();
                    //                await sbSource.SetBitmapAsync(frame);
                    //                PreviewFrameImage.Source = sbSource;
                }
            }
            else
            {
                _indexImageFile = (int) (frameID % _imageFiles.LongCount());
                using (IRandomAccessStreamWithContentType stream = await _imageFiles[_indexImageFile].OpenReadAsync())
                {
                    imageData = new byte[stream.Size];
                    using (DataReader reader = new DataReader(stream))
                    {
                        await reader.LoadAsync((uint)stream.Size);
                        reader.ReadBytes(imageData);
                    }
                }
            }

            // Stream image to the server
            if (_tokenController.GetCurrentToken() > 0)
            {
                if (Const.IS_EXPERIMENT)
                {
                    dataTime = GetTimeMillis();
                    var unused = await Bitmap2JPEG(_imageBitmapsCompress[_indexImageFileCompress]);
                    _indexImageFileCompress = (_indexImageFileCompress + 1) % _imageFileCompressLength;
                    compressedTime = GetTimeMillis();
                }
                _tokenController.DecreaseToken();
                _tokenController.LogSentPacket(frameID, dataTime, compressedTime);
                await StreamImageAsync(imageData, frameID);
            }
        }

        /// <summary>
        /// Cleans up the camera resources (after stopping the preview if necessary) and unregisters from MediaCapture events
        /// </summary>
        /// <returns></returns>
        private async Task CleanupCameraAsync()
        {
            if (_isInitialized)
            {
                if (_isPreviewing)
                {
                    // The call to stop the preview is included here for completeness, but can be
                    // safely removed if a call to MediaCapture.Dispose() is being made later,
                    // as the preview will be automatically stopped at that point
                    await StopPreviewAsync();
                }

                _isInitialized = false;
            }

            if (_mediaCapture != null)
            {
                _mediaCapture.Failed -= MediaCapture_Failed;
                _mediaCapture.Dispose();
                _mediaCapture = null;
            }
        }

        #endregion MediaCapture methods


        #region Network methods

        private async Task InitializeNetworkAsync()
        {
            // Everything is hard coded for now
            HostName serverHost = new HostName(Const.SERVER_IP);

            _controlSocket = new StreamSocket();            
            await _controlSocket.ConnectAsync(serverHost, "" + Const.CONTROL_PORT);
            _controlSocketReader = new DataReader(_controlSocket.InputStream);
            _controlSocketWriter = new DataWriter(_controlSocket.OutputStream);

            _videoStreamSocket = new StreamSocket();
            _videoStreamSocket.Control.QualityOfService = SocketQualityOfService.LowLatency;
            await _videoStreamSocket.ConnectAsync(serverHost, "" + Const.VIDEO_STREAM_PORT);
            _videoStreamSocketReader= new DataReader(_videoStreamSocket.InputStream);
            _videoStreamSocketWriter= new DataWriter(_videoStreamSocket.OutputStream);

            _resultReceivingSocket = new StreamSocket();
            _resultReceivingSocket.Control.QualityOfService = SocketQualityOfService.LowLatency;
            await _resultReceivingSocket.ConnectAsync(serverHost, "" + Const.RESULT_RECEIVING_PORT);
            _resultReceivingSocketReader= new DataReader(_resultReceivingSocket.InputStream);
            _resultReceivingSocketWriter= new DataWriter(_resultReceivingSocket.OutputStream);

            StartResultReceivingThread();
        }

        private void StartResultReceivingThread()
        {
            IAsyncAction asyncAction = Windows.System.Threading.ThreadPool.RunAsync(
                async (workItem) =>
            {
                while (true)
                {
                    // receive current time at server
                    string recvMsg = await receiveMsgAsync(_resultReceivingSocketReader);
                    Debug.WriteLine(recvMsg);

                    JsonObject obj = JsonValue.Parse(recvMsg).GetObject();
                    string status = null;
                    string result = null;
                    long frameID = -1;
                    string engineID = null;
                    try
                    {
                        status = obj.GetNamedString("status");
                        result = obj.GetNamedString("result");
                        frameID = (long)obj.GetNamedNumber("frame_id");
                        engineID = obj.GetNamedString("engine_id");
                    }
                    catch (Exception)
                    {
                        Debug.WriteLine("the return message has no status field");
                        return;
                    }

                    ReceivedPacketInfo receivedPacketInfo = new ReceivedPacketInfo(frameID, engineID, status);
                    receivedPacketInfo.setMsgRecvTime(GetTimeMillis());
                    if (!status.Equals("success"))
                    {
                        receivedPacketInfo.setGuidanceDoneTime(GetTimeMillis());
                        await _tokenController.ProcessReceivedPacket(receivedPacketInfo);
                        continue;
                    }

                    if (result != null)
                    {
                        /* parsing result */
                        JsonObject resultJSON = JsonValue.Parse(result).GetObject();

                        // image guidance
                        string imageFeedbackString = null;
                        try
                        {
                            imageFeedbackString = resultJSON.GetNamedString("image");
                        }
                        catch (Exception)
                        {
                            Debug.WriteLine("no image guidance found");
                        }
                        if (imageFeedbackString != null)
                        {
                            IBuffer buffer = CryptographicBuffer.DecodeFromBase64String(imageFeedbackString);
                            //byte[] imageFeedbackBytes;
                            //CryptographicBuffer.CopyToByteArray(buffer, out imageFeedbackBytes);
                            using (var stream = new InMemoryRandomAccessStream())
                            {
                                using (var dataWriter = new DataWriter(stream))
                                {
                                    dataWriter.WriteBuffer(buffer);
                                    await dataWriter.StoreAsync();
                                    BitmapDecoder decoder = await BitmapDecoder.CreateAsync(stream);
                                    SoftwareBitmap imageFeedback = await decoder.GetSoftwareBitmapAsync();
                                    SoftwareBitmap imageFeedbackDisplay = SoftwareBitmap.Convert(imageFeedback, BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied);
                                    await Windows.ApplicationModel.Core.CoreApplication.MainView.CoreWindow.Dispatcher.RunAsync(CoreDispatcherPriority.Normal, async () => {
                                        var sbSource = new SoftwareBitmapSource();
                                        await sbSource.SetBitmapAsync(imageFeedbackDisplay);
                                        GuidanceImage.Source = sbSource;
                                    });
                                }
                            }
                        }
                        // speech guidance
                        string speechFeedback = null;
                        try
                        {
                            speechFeedback = resultJSON.GetNamedString("speech");
                        }
                        catch (Exception)
                        {
                            Debug.WriteLine("no speech guidance found");
                        }

                        if (speechFeedback != null)
                        {
                            SpeechSynthesisStream stream = null;
                            using (SpeechSynthesizer synthesizer = new SpeechSynthesizer())
                            {
                                stream = await synthesizer.SynthesizeTextToStreamAsync(speechFeedback);
                            }

                            // Send the stream to the media object.
                            await Windows.ApplicationModel.Core.CoreApplication.MainView.CoreWindow.Dispatcher.RunAsync(CoreDispatcherPriority.Normal, () => {
                                _mediaElement.SetSource(stream, stream.ContentType);
                                _mediaElement.Play();
                            });
                            
                        }

                        receivedPacketInfo.setGuidanceDoneTime(GetTimeMillis());
                        await _tokenController.ProcessReceivedPacket(receivedPacketInfo);
                    }
                }
            });

            // A reference to the work item is cached so that we can trigger a
            // cancellation when the user presses the Cancel button.
            _resultReceivingWorkItem = asyncAction;
        }

        private async Task StreamImageAsync(byte[] imageData, long frameID)
        {
//            Debug.WriteLine("streaming image");
           
            string headerData = "{\"" + "frame_id" + "\":" + frameID + "}";
            _videoStreamSocketWriter.WriteInt32(checked((int)_videoStreamSocketWriter.MeasureString(headerData)));
            _videoStreamSocketWriter.WriteString(headerData);
            _videoStreamSocketWriter.WriteInt32(imageData.Length);
            _videoStreamSocketWriter.WriteBytes(imageData);
            await _videoStreamSocketWriter.StoreAsync();
        }

        private async Task GetServerTimeOffsetAsync()
        {
            long min_diff = 1000000;
            long bestSentTime = 0, bestServerTime = 0, bestRecvTime = 0;
            for (int i = 0; i < Const.MAX_PING_TIMES; i++)
            {
                // send current time to server
                long sentTime = GetTimeMillis();
                //byte[] jsonData = Encoding.ASCII.GetBytes("{\"sync_time\":" + sentTime + "}");
                string jsonData = "{\"sync_time\":" + sentTime + "}";
                _controlSocketWriter.WriteInt32(checked((int)_controlSocketWriter.MeasureString(jsonData)));
                _controlSocketWriter.WriteString(jsonData);
                await _controlSocketWriter.StoreAsync();

                // receive current time at server
                string recvMsg = await receiveMsgAsync(_controlSocketReader);
                
                JsonObject obj = JsonValue.Parse(recvMsg).GetObject();
                long serverTime = (long) obj.GetNamedNumber("sync_time");

                long recvTime = GetTimeMillis();
                if (recvTime - sentTime < min_diff)
                {
                    min_diff = recvTime - sentTime;
                    bestSentTime = sentTime;
                    bestServerTime = serverTime;
                    bestRecvTime = recvTime;
                }
            }
            string sync_str = "" + bestSentTime + "\t" + bestServerTime + "\t" + bestRecvTime + "\n";
            await _myLogger.WriteString(sync_str);
            Debug.WriteLine(sync_str);
        }

        private async Task<string> receiveMsgAsync(DataReader reader)
        {
            uint sizeFieldCount = await reader.LoadAsync(4);
            if (sizeFieldCount != 4)
            {
                // The underlying socket was closed before we were able to read the whole data.
                return null;
            }
            int retLength = reader.ReadInt32();
            uint actualRetLength = await reader.LoadAsync((uint)retLength);
            if (retLength != actualRetLength)
            {
                // The underlying socket was closed before we were able to read the whole data.
                return null;
            }
            string retData = reader.ReadString(actualRetLength);
            return retData;
        }

        #endregion Network methods


        #region Helper functions

        /// <summary>
        /// Queries the available video capture devices to try and find one mounted on the desired panel
        /// </summary>
        /// <param name="desiredPanel">The panel on the device that the desired camera is mounted on</param>
        /// <returns>A DeviceInformation instance with a reference to the camera mounted on the desired panel if available,
        ///          any other camera if not, or null if no camera is available.</returns>
        private static async Task<DeviceInformation> FindCameraDeviceByPanelAsync(Windows.Devices.Enumeration.Panel desiredPanel)
        {
            // Get available devices for capturing pictures
            var allVideoDevices = await DeviceInformation.FindAllAsync(DeviceClass.VideoCapture);

            // Get the desired camera by panel
            DeviceInformation desiredDevice = allVideoDevices.FirstOrDefault(x => x.EnclosureLocation != null && x.EnclosureLocation.Panel == desiredPanel);

            // If there is no device mounted on the desired panel, return the first device found
            return desiredDevice ?? allVideoDevices.FirstOrDefault();
        }

        private long GetTimeMillis()
        {
            //return DateTime.Now.Ticks / TimeSpan.TicksPerMillisecond
            return (long) (Stopwatch.GetTimestamp() * (1000.0 / Stopwatch.Frequency));
        }

        public class HiResDateTime
        {
            private static DateTime _startTime;
            private static Stopwatch _stopWatch = null;
            private static TimeSpan _maxIdle =
                TimeSpan.FromSeconds(10);

            public static DateTime UtcNow
            {
                get
                {
                    if ((_stopWatch == null) ||
                        (_startTime.Add(_maxIdle) < DateTime.UtcNow))
                    {
                        Reset();
                    }
                    return _startTime.AddTicks(_stopWatch.Elapsed.Ticks);
                }
            }

            private static void Reset()
            {
                _startTime = DateTime.UtcNow;
                _stopWatch = Stopwatch.StartNew();
            }
        }

        private async Task<IReadOnlyList<StorageFile>> GetImageFiles(StorageFolder root, string subFolderName)
        {
            Debug.WriteLine("Get image files from: " + subFolderName);
            StorageFolder imageFolder;
            try
            {
                imageFolder = await root.GetFolderAsync(subFolderName);
            }
            catch (System.IO.FileNotFoundException)
            {
                var zipFile = await root.GetFileAsync(subFolderName + ".zip");
                await Task.Run(() =>
                {
                    Task.Yield();
                    ZipFile.ExtractToDirectory(zipFile.Path, root.Path);
                });
                imageFolder = await root.GetFolderAsync(subFolderName);
            }
            
            IReadOnlyList<StorageFile> imageFiles = await imageFolder.GetFilesAsync();
            return imageFiles;
        }

        private async Task<byte[]> Bitmap2JPEG(SoftwareBitmap bitmap)
        {
            // JPEG quality
            var propertySet = new BitmapPropertySet();
            var qualityValue = new BitmapTypedValue(
                0.67, // Maximum quality
                PropertyType.Single
                );
            propertySet.Add("ImageQuality", qualityValue);

            // JPEG compression into memory
            var memStream = new InMemoryRandomAccessStream();
            BitmapEncoder encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.JpegEncoderId, memStream, propertySet);
            // the SetSoftwareBitmap may cause a memory leak, but only for debugging (it's a bug in VS)
            // See http://stackoverflow.com/questions/35528030/bitmapencoder-memoryleak-with-softwarebitmap
            encoder.SetSoftwareBitmap(bitmap);
            await encoder.FlushAsync();

            // Send the data to network
            byte[] imageData;
            using (var inputStream = memStream.GetInputStreamAt(0))
            {
                using (var dataReader = new DataReader(inputStream))
                {
                    // Once we have written the contents successfully we load the stream.
                    await dataReader.LoadAsync((uint)memStream.Size);
                    imageData = new byte[(uint)memStream.Size];
                    dataReader.ReadBytes(imageData);
                }
            }

            // Clean...
            memStream.Dispose();

            return imageData;
        }

        #endregion Helper functions 

    }
}
