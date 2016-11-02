using UnityEngine;
using System.Collections;
using HoloToolkit.Unity;
using UnityEngine.VR.WSA.WebCam;
using System.IO;

#if !UNITY_EDITOR
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
#endif

namespace gabriel_client
{
    public class GuidanceControl : Singleton<GuidanceControl>
    {
#if !UNITY_EDITOR
        // state variables
        private bool _isInitialized = false;

        // Information about the camera device
        private bool _mirroringPreview = false;

        // Timer to process (transmit) preview frame periodically
        private DispatcherTimer _getFrameTimer;

        // State variable to make sure no timer tasks are overlapped
        private bool _isDoingTimerTask = false;
        private System.Object _timerTaskLock = new System.Object();

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
        private System.Object _frameIdLock = new System.Object();
        private TokenController _tokenController;

        // Feedback
        MediaElement _mediaElement;

        // Logger
        private MyLogger _myLogger;

        // Timer to stop program when doing experiments
        private DispatcherTimer _stopTimer;

        // PhotoCapture
        private PhotoCapture _photoCaptureObject = null;
        private Resolution _captureResolution;

        // Synchronize data between threads
        private byte[] _imageDataRaw;
        private System.Object _imageLock = new System.Object();
        private bool _frameReadyFlag = false;
#endif

#if !UNITY_EDITOR
        async void Start()
        {   
            // Initialize logger
            _myLogger = new MyLogger("latency-" + Const.SERVER_IP + "-" + Const.TOKEN_SIZE + ".txt");
            await _myLogger.InitializeLogger();

            // Initialize token control
            _tokenController = new TokenController(Const.TOKEN_SIZE, _myLogger);

            // Prepare TTS
            // Unity app cannot access MediaElement: http://www.roadtoholo.com/2016/05/04/1601/text-to-speech-for-hololens/
            //_mediaElement = new MediaElement();
            
            // Initialize file loaders
            await InitializeFileLoading();
            
            // Initialize network
            await InitializeNetworkAsync();
            await GetServerTimeOffsetAsync();

            _isInitialized = true;
        }
#else
        void Start()
        {
        }
#endif

#if !UNITY_EDITOR
        void Update()
        {
            UnityEngine.Debug.Log("Update Called");       

            if (!_isInitialized) return;   
            
            bool tempFlag = false;
            lock (_timerTaskLock)
            {
                if (_isDoingTimerTask == false)
                {
                    _isDoingTimerTask = true;
                    _frameID++;
                    UnityEngine.Debug.Log("id: " + _frameID);
                    tempFlag = true;
                }
            }
            if (tempFlag)
            {
                // Initialize camera
                PhotoCapture.CreateAsync(false, OnPhotoCaptureCreated);
            }

        }
#else
        void Update()
        {
        }
#endif


#if !UNITY_EDITOR
        #region Event handlers

        void OnPhotoCaptureCreated(PhotoCapture captureObject)
        {
            //UnityEngine.Debug.Log("++OnPhotoCaptureCreated");
            _photoCaptureObject = captureObject;

            _captureResolution = PhotoCapture.SupportedResolutions.OrderByDescending((res) => res.width * res.height).First();

            CameraParameters c = new CameraParameters();
            c.hologramOpacity = 0.0f;
            c.cameraResolutionWidth = _captureResolution.width;
            c.cameraResolutionHeight = _captureResolution.height;
            c.pixelFormat = CapturePixelFormat.BGRA32;

            captureObject.StartPhotoModeAsync(c, false, OnPhotoModeStarted);
        }

        void OnStoppedPhotoMode(PhotoCapture.PhotoCaptureResult result)
        {
            UnityEngine.Debug.Log("++OnStoppedPhotoMode");
            _photoCaptureObject.Dispose();
            _photoCaptureObject = null;
        }

        private void OnPhotoModeStarted(PhotoCapture.PhotoCaptureResult result)
        {
            //UnityEngine.Debug.Log("++OnPhotoModeStarted");
            if (result.success)
            {
                _photoCaptureObject.TakePhotoAsync(OnCapturedPhotoToMemory);
            }
            else
            {
                UnityEngine.Debug.Log("Unable to start photo mode!");
            }
        }

        void OnCapturedPhotoToMemory(PhotoCapture.PhotoCaptureResult result, PhotoCaptureFrame photoCaptureFrame)
        {
            UnityEngine.Debug.Log("++OnCapturedPhotoToMemory");
            if (result.success)
            {
                if (!Const.LOAD_IMAGES)
                {
                    List<byte> imageBufferList = new List<byte>();
                    // Copy the raw IMFMediaBuffer data into our empty byte list.
                    photoCaptureFrame.CopyRawImageDataIntoBuffer(imageBufferList);
                    {
                        _imageDataRaw = imageBufferList.ToArray();
                        _frameReadyFlag = true;
                    }
                }
                else
                {
                    /*
                    _indexImageFile = (int)(frameID % _imageFiles.LongCount());
                    using (IRandomAccessStreamWithContentType stream = await _imageFiles[_indexImageFile].OpenReadAsync())
                    {
                        imageData = new byte[stream.Size];
                        using (DataReader reader = new DataReader(stream))
                        {
                            await reader.LoadAsync((uint)stream.Size);
                            reader.ReadBytes(imageData);
                        }
                    }
                    */
                }
            }
            _photoCaptureObject.StopPhotoModeAsync(OnStoppedPhotoMode);
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
                    UnityEngine.Debug.Log("test image directory empty!");
                }
                else
                {
                    UnityEngine.Debug.Log("Number of image files in the input folder: " + _imageFiles.LongCount());
                }
                _indexImageFile = 0;
            }
        }

        #endregion File loading methods
        
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
            _videoStreamSocketReader = new DataReader(_videoStreamSocket.InputStream);
            _videoStreamSocketWriter = new DataWriter(_videoStreamSocket.OutputStream);

            _resultReceivingSocket = new StreamSocket();
            _resultReceivingSocket.Control.QualityOfService = SocketQualityOfService.LowLatency;
            await _resultReceivingSocket.ConnectAsync(serverHost, "" + Const.RESULT_RECEIVING_PORT);
            _resultReceivingSocketReader = new DataReader(_resultReceivingSocket.InputStream);
            _resultReceivingSocketWriter = new DataWriter(_resultReceivingSocket.OutputStream);

            StartVideoStreamingThread();

            StartResultReceivingThread();
        }

        private void StartVideoStreamingThread()
        {
            IAsyncAction asyncAction = Windows.System.Threading.ThreadPool.RunAsync(
                async (workItem) =>
                {
                    while (true)
                    {
                        UnityEngine.Debug.Log("Check new frame");

                        if (!_frameReadyFlag) continue;

                        {
                            byte[] imageData = null; // after compression

                            long dataTime = 0, compressedTime = 0;
                            if (!Const.LOAD_IMAGES)
                            {
                                // Compress to JPEG bytes
                                imageData = await Bitmap2JPEG(_imageDataRaw);
                            }
                            else
                            {
                            }

                            // Stream image to the server
                            if (_tokenController.GetCurrentToken() > 0)
                            {
                                _tokenController.DecreaseToken();
                                _tokenController.LogSentPacket(_frameID, dataTime, compressedTime);
                                await StreamImageAsync(imageData, _frameID);
                            }

                            _frameReadyFlag = false;
                            _isDoingTimerTask = false;
                        }
                    }
                });
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
                        UnityEngine.Debug.Log(recvMsg);

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
                            UnityEngine.Debug.Log("the return message has no status field");
                            return;
                        }

                        ReceivedPacketInfo receivedPacketInfo = new ReceivedPacketInfo(frameID, engineID, status);
                        receivedPacketInfo.setMsgRecvTime(GetTimeMillis());
                        if (!status.Equals("success"))
                        {
                            receivedPacketInfo.setGuidanceDoneTime(GetTimeMillis());
                            _tokenController.ProcessReceivedPacket(receivedPacketInfo);
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
                                UnityEngine.Debug.Log("no image guidance found");
                            }
                            if (imageFeedbackString != null)
                            {
                                IBuffer buffer = CryptographicBuffer.DecodeFromBase64String(imageFeedbackString);
                                /*
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
//                                            GuidanceImage.Source = sbSource;
                                        });
                                    }
                                }
                                */
                            }
                            // speech guidance
                            string speechFeedback = null;
                            try
                            {
                                speechFeedback = resultJSON.GetNamedString("speech");
                            }
                            catch (Exception)
                            {
                                UnityEngine.Debug.Log("no speech guidance found");
                            }
                            if (speechFeedback != null)
                            {
                                /*
                                SpeechSynthesisStream stream = null;
                                using (SpeechSynthesizer synthesizer = new SpeechSynthesizer())
                                {
                                    stream = await synthesizer.SynthesizeTextToStreamAsync(speechFeedback);
                                }
                                */
                            }

                            receivedPacketInfo.setGuidanceDoneTime(GetTimeMillis());
                            _tokenController.ProcessReceivedPacket(receivedPacketInfo);
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
                long serverTime = (long)obj.GetNamedNumber("sync_time");

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
            UnityEngine.Debug.Log(sync_str);
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
            return (long)(Stopwatch.GetTimestamp() * (1000.0 / Stopwatch.Frequency));
        }
        
        private async Task<IReadOnlyList<StorageFile>> GetImageFiles(StorageFolder root, string subFolderName)
        {
            UnityEngine.Debug.Log("Get image files from: " + subFolderName);
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

        private async Task<byte[]> Bitmap2JPEG(byte[] dataRaw)
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
            encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, (uint) _captureResolution.width, (uint) _captureResolution.height, 300, 300, dataRaw);
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
#endif
    }
}
