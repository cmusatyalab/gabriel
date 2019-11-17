using UnityEngine;
using System.Collections;
using HoloToolkit.Unity;
using UnityEngine.VR.WSA.WebCam;
using System.IO;
using UnityEngine.UI;
using System.Collections.Concurrent;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;

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
        // Game objects
        public GameObject Cube;
        public GameObject Cylinder;

        public GameObject Bread;
        public GameObject Ham;
        public GameObject Lettuce;
        public GameObject Tomato;
        public GameObject Breadtop;

        // TTS
        // The UWP way of doing this using MediaElement cannot work: Unity app cannot access MediaElement:
        // http://www.roadtoholo.com/2016/05/04/1601/text-to-speech-for-hololens/
        // (this has been merged into Holotoolkit-Unity)
        public TextToSpeechManager textToSpeechManager;

        // Text display
        public Text textDisplay;

#if !UNITY_EDITOR
        // state variables
        private bool _isInitialized = false;

        // State variable to make sure no timer tasks are overlapped
        private bool _isDoingTimerTask = false;
        private bool _isCapturing = false;
        private bool _isCameraReady = false;
        private System.Object _timerTaskLock = new System.Object();

        // File operations
        private IReadOnlyList<StorageFile> _imageFiles;

        // PhotoCapture
        private PhotoCapture _photoCaptureObject = null;
        private Resolution _captureResolution;
        private CameraParameters _cameraPara;
        private bool _isHoloCaptureFrame = false;

        // Synchronize data between threads
        private byte[] _imageDataRaw;
        Matrix4x4 _cameraToWorldMatrix, _projectionMatrix;
        private System.Object _imageLock = new System.Object();
        private bool _frameReadyFlag = false;

        // Guidance
        private string _speechFeedback = null;
        private bool _guidancePosReady = false;
        private bool _useDefaultStabilizationPlane = true;
        private Vector3 _guidancePos = new Vector3(0, 0, 0);

        private long _frameID = 0;

        private enum Hologram
        {
            Bread = 1,
            Ham = 2,
            Lettuce = 3,
            Half = 4,
            Tomato = 5,
            Full = 6,
            BreadTop = 7,
            None = 8
        }

        class Matricies
        {
            public Matrix4x4 cameraToWorldMatrix;
            public Matrix4x4 projectionMatrix;

            public Matricies(Matrix4x4 cameraToWorldMatrix, Matrix4x4 projectionMatrix)
            {
                this.cameraToWorldMatrix = cameraToWorldMatrix;
                this.projectionMatrix = projectionMatrix;
            }
        }

        private ConcurrentDictionary<long, Matricies> _frameIdMatriciesMap = new ConcurrentDictionary<long, Matricies>();

        private Hologram _hologram;
        private Hologram _previousHologram;
        private Instruction.EngineFields _engineFields = new Instruction.EngineFields();

        // Performance measurements
        private int _fpsCounter = 0;
        private long _startTime = 0;

        // Network
        private Windows.Networking.Sockets.MessageWebSocket _messageWebSocket;
        private int _numTokens = 0;
        private System.Object _tokenLock = new System.Object();
#endif

#if !UNITY_EDITOR
        async void Start()
        {

            // Initialize camera and camera parameters
            _captureResolution = PhotoCapture.SupportedResolutions.OrderBy((res) => res.width * res.height).First();

            _cameraPara = new CameraParameters();
            _cameraPara.hologramOpacity = 0.0f;
            _cameraPara.cameraResolutionWidth = _captureResolution.width;
            _cameraPara.cameraResolutionHeight = _captureResolution.height;
            _cameraPara.pixelFormat = CapturePixelFormat.JPEG;

            if (Const.HOLO_CAPTURE)
            {
                PhotoCapture.CreateAsync(true, OnPhotoCaptureCreatedHOLO);
            }
            else
            {
                PhotoCapture.CreateAsync(false, OnPhotoCaptureCreated);
            }

            // Initialize file loaders
            await InitializeFileLoading();

            // Initialize network
            await SetupWebsocket();

            _isInitialized = true;
            _startTime = GetTimeMillis();
        }
#else
        void Start()
        {
        }
#endif

#if !UNITY_EDITOR
        void Update()
        {
            //UnityEngine.Debug.Log("Update Called");

            // Performance measurements
            //_fpsCounter++;
            //if (_fpsCounter % 100 == 0)
            //    UnityEngine.Debug.Log("fps: " + (_fpsCounter * 1000 / (GetTimeMillis() - _startTime)));

            if (!_isInitialized) return;

            if (_isCameraReady && !_isDoingTimerTask && !_isCapturing)
            {
                _isDoingTimerTask = true;
                _isCapturing = true;

                // Start capture
                if (Const.HOLO_CAPTURE)
                {
                    if (_isHoloCaptureFrame)
                        _cameraPara.hologramOpacity = 0.9f;
                    else
                        _cameraPara.hologramOpacity = 0.0f;
                    _photoCaptureObject.StartPhotoModeAsync(_cameraPara, false, OnPhotoModeStartedHOLO);
                }
                else
                {
                    _photoCaptureObject.TakePhotoAsync(OnProcessFrame);
                }
            }

            // Display the verbal guidance on the panel
            textDisplay.text = _speechFeedback;

            // Place the object at the calculated position.
            if (_guidancePosReady)
            {
                Vector3 tempGuidancePos = _guidancePos;// + new Vector3(0, Cylinder.transform.localScale.y / 1.8f, 0);
                Vector3 tempRight = Camera.main.transform.right;
                if (_previousHologram == _hologram)
                {
                    if (Vector3.Distance(gameObject.transform.position, tempGuidancePos) < 0.05)
                    {
                        gameObject.transform.position = gameObject.transform.position * 0.95f + tempGuidancePos * 0.05f;
                        gameObject.transform.right = gameObject.transform.right * 0.95f + tempRight * 0.05f;
                    }
                    else
                    {
                        gameObject.transform.position = tempGuidancePos;
                        gameObject.transform.right = tempRight;
                    }
                }
                else
                {
                    switch (_previousHologram)
                    {
                        case Hologram.Bread:
                            Bread.SetActive(false);
                            break;
                        case Hologram.Ham:
                            Ham.SetActive(false);
                            break;
                        case Hologram.Lettuce:
                            Lettuce.SetActive(false);
                            break;
                        case Hologram.Tomato:
                            Tomato.SetActive(false);
                            break;
                        case Hologram.BreadTop:
                            Breadtop.SetActive(false);
                            break;
                        default:
                            break;
                    }
                    _useDefaultStabilizationPlane = false;
                    switch (_hologram)
                    {
                        case Hologram.Bread:
                            Bread.SetActive(true);
                            break;
                        case Hologram.Ham:
                            Ham.SetActive(true);
                            break;
                        case Hologram.Lettuce:
                            Lettuce.SetActive(true);
                            break;
                        case Hologram.Tomato:
                            Tomato.SetActive(true);
                            break;
                        case Hologram.BreadTop:
                            Breadtop.SetActive(true);
                            break;
                        default:
                            _useDefaultStabilizationPlane = true;
                            break;
                    }
                    _previousHologram = _hologram;
                    gameObject.transform.position = tempGuidancePos;
                    gameObject.transform.right = tempRight;
                }
                _guidancePosReady = false;
            }

            if (!_useDefaultStabilizationPlane)
            {
                var normal = -Camera.main.transform.forward;
                var position = gameObject.transform.position;
                UnityEngine.VR.WSA.HolographicSettings.SetFocusPointForFrame(position);
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
            _photoCaptureObject.StartPhotoModeAsync(_cameraPara, false, OnPhotoModeStarted);
        }

        void OnPhotoCaptureCreatedHOLO(PhotoCapture captureObject)
        {
            //UnityEngine.Debug.Log("++OnPhotoCaptureCreatedHOLO");
            _photoCaptureObject = captureObject;
            _isCameraReady = true;
        }

        private void OnPhotoModeStarted(PhotoCapture.PhotoCaptureResult result)
        {
            //UnityEngine.Debug.Log("++OnPhotoModeStarted");
            if (result.success)
            {
                _isCameraReady = true;
            }
            else
            {
                UnityEngine.Debug.Log("Unable to start photo mode!");
            }
        }

        private void OnPhotoModeStartedHOLO(PhotoCapture.PhotoCaptureResult result)
        {
            //UnityEngine.Debug.Log("++OnPhotoModeStartedHOLO");
            if (result.success)
            {
                _photoCaptureObject.TakePhotoAsync(OnProcessFrame);
            }
            else
            {
                UnityEngine.Debug.Log("Unable to start photo mode!");
            }
        }

        void OnProcessFrame(PhotoCapture.PhotoCaptureResult result, PhotoCaptureFrame photoCaptureFrame)
        {
            UnityEngine.Debug.Log("++OnProcessFrame");
            if (result.success)
            {
                if (!Const.LOAD_IMAGES)
                {
                    List<byte> imageBufferList = new List<byte>();
                    // Copy the raw IMFMediaBuffer data into our empty byte list.
                    photoCaptureFrame.CopyRawImageDataIntoBuffer(imageBufferList);

                    photoCaptureFrame.TryGetCameraToWorldMatrix(out _cameraToWorldMatrix);
                    photoCaptureFrame.TryGetProjectionMatrix(out _projectionMatrix);
                    //UnityEngine.Debug.Log(cameraToWorldMatrix);

                    photoCaptureFrame.Dispose();

                    _imageDataRaw = imageBufferList.ToArray();
                    _frameReadyFlag = true;
                }
            }
            if (Const.HOLO_CAPTURE)
            {
                _photoCaptureObject.StopPhotoModeAsync(OnStoppedPhotoModeHOLO);
            }
            else
            {
                _isCapturing = false;
            }
        }

        void OnStoppedPhotoMode(PhotoCapture.PhotoCaptureResult result)
        {
            UnityEngine.Debug.Log("++OnStoppedPhotoMode");
            _photoCaptureObject.Dispose();
            _photoCaptureObject = null;
        }

        void OnStoppedPhotoModeHOLO(PhotoCapture.PhotoCaptureResult result)
        {
            //UnityEngine.Debug.Log("++OnStoppedPhotoModeHOLO");
            _isCapturing = false;
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
            }
        }

        #endregion File loading methods

        #region Network methods

        private async Task SetupWebsocket()
        {
            this._messageWebSocket = new Windows.Networking.Sockets.MessageWebSocket();

            this._messageWebSocket.MessageReceived += WebSocket_MessageReceived;
            this._messageWebSocket.Closed += WebSocket_Closed;

            await this._messageWebSocket.ConnectAsync(new Uri("ws://" + Const.SERVER_IP + ":" + Const.PORT));
            StartVideoStreamingThread();
        }

        private void UpdateHologram(Instruction.EngineFields engineFields, long frameID)
        {
            switch (engineFields.Sandwich.State)
            {
                case Instruction.Sandwich.Types.State.Bread:
                    _hologram = Hologram.Ham;
                    break;
                case Instruction.Sandwich.Types.State.Ham:
                    _hologram = Hologram.Lettuce;
                    break;
                case Instruction.Sandwich.Types.State.Lettuce:
                    _hologram = Hologram.Bread;
                    break;
                case Instruction.Sandwich.Types.State.Half:
                    _hologram = Hologram.Tomato;
                    break;
                case Instruction.Sandwich.Types.State.Tomato:
                    _hologram = Hologram.BreadTop;
                    break;
                default:
                    _hologram = Hologram.None;
                    _guidancePosReady = true;
                    return;
            }

            UnityEngine.Debug.Log("Hologram x: " + engineFields.Sandwich.HoloX);
            UnityEngine.Debug.Log("Hologram y: " + engineFields.Sandwich.HoloY);
            UnityEngine.Debug.Log("Hologram depth: " + engineFields.Sandwich.HoloDepth);


            Matricies matricies;
            _frameIdMatriciesMap.TryRemove(frameID, out matricies);
            _guidancePos = Pixel2WorldPos(
                (float)engineFields.Sandwich.HoloX, (float)engineFields.Sandwich.HoloY, (float)engineFields.Sandwich.HoloDepth, 
                matricies.projectionMatrix, matricies.cameraToWorldMatrix);

            _guidancePosReady = true;
        }

        private void WebSocket_MessageReceived(Windows.Networking.Sockets.MessageWebSocket sender, Windows.Networking.Sockets.MessageWebSocketMessageReceivedEventArgs args)
        {
            using (DataReader dataReader = args.GetDataReader())
            {
                byte[] bytes = new byte[dataReader.UnconsumedBufferLength];
                dataReader.ReadBytes(bytes);

                Gabriel.ToClient toClient = Gabriel.ToClient.Parser.ParseFrom(bytes);

                if (toClient.ResultWrapper == null)
                {
                    // Set num tokens on welcome message
                    lock (_tokenLock)
                    {
                        _numTokens = toClient.NumTokens;
                    }
                    return;
                }

                // We only return one to avoid race conditions when we have multiple messages in flight
                lock (_tokenLock)
                {
                    _numTokens++;
                }

                Gabriel.ResultWrapper resultWrapper = toClient.ResultWrapper;
                if (resultWrapper.Status != Gabriel.ResultWrapper.Types.Status.Success)
                {
                    UnityEngine.Debug.Log("Output status was: " + resultWrapper.Status);
                    return;
                }

                Instruction.EngineFields newEngineFields = resultWrapper.EngineFields.Unpack<Instruction.EngineFields>();
                if (newEngineFields.UpdateCount <= _engineFields.UpdateCount)
                {
                    // There was no update or there was an update based on a stale frame
                    return;
                }

                for (int i = 0; i < resultWrapper.Results.Count(); i++)
                {
                    Gabriel.ResultWrapper.Types.Result result = resultWrapper.Results[i];

                    if (!result.EngineName.Equals(Const.ENGINE_NAME))
                    {
                        UnityEngine.Debug.LogError("Got result from engine " + result.EngineName);
                    }

                    if (result.PayloadType == Gabriel.PayloadType.Text)
                    {
                        _speechFeedback = result.Payload.ToStringUtf8();
                        textToSpeechManager.SpeakText(_speechFeedback);
                    }
                }

                _engineFields = newEngineFields;
                UpdateHologram(newEngineFields, resultWrapper.FrameId);
            }
        }

        private void WebSocket_Closed(Windows.Networking.Sockets.IWebSocket sender, Windows.Networking.Sockets.WebSocketClosedEventArgs args)
        {
            UnityEngine.Debug.Log("WebSocket_Closed; Code: " + args.Code + ", Reason: \"" + args.Reason + "\"");
            CoreApplication.Exit();
        }

        private void StartVideoStreamingThread()
        {
            IAsyncAction asyncAction = Windows.System.Threading.ThreadPool.RunAsync(
                async (workItem) =>
                {
                    while (true)
                    {
                        if (!_frameReadyFlag)
                        {
                            await System.Threading.Tasks.Task.Delay(30);
                            continue;
                        }

                        {
                            byte[] imageData = null; // after compression
                            if (!Const.LOAD_IMAGES)
                            {
                                imageData = _imageDataRaw;
                                // Compress to JPEG bytes
                                //imageData = await Bitmap2JPEG(_imageDataRaw);
                            }

                            // Stream image to the server
                            if (_numTokens > 0)
                            {
                                lock (_tokenLock)
                                {
                                    _numTokens--;
                                }

                                Matricies matricies = new Matricies(_cameraToWorldMatrix, _projectionMatrix);
                                _frameIdMatriciesMap.TryAdd(_frameID, matricies);

                                Gabriel.FromClient fromClient = new Gabriel.FromClient
                                {
                                    PayloadType = Gabriel.PayloadType.Image,
                                    EngineName = Const.ENGINE_NAME,
                                    Payload = ByteString.CopyFrom(imageData),
                                    EngineFields = Any.Pack(_engineFields),
                                    FrameId = _frameID
                                };

                                await SendMessageUsingMessageWebSocketAsync(fromClient);
                                _frameID++;
                            }

                            _frameReadyFlag = false;
                            _isDoingTimerTask = false;
                        }
                    }
                });
        }

        private async Task SendMessageUsingMessageWebSocketAsync(Gabriel.FromClient fromClient)
        {
            using (var dataWriter = new DataWriter(this._messageWebSocket.OutputStream))
            {
                dataWriter.WriteBytes(fromClient.ToByteArray());
                await dataWriter.StoreAsync();
                dataWriter.DetachStream();
            }
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

        private Vector3 Pixel2WorldPos(float x, float y, float depth, Matrix4x4 projectionMatrix, Matrix4x4 cameraToWorldMatrix)
        {
            //TODO: the below operations could probably be replaced by the ScreenPointToRay function call
            Vector2 ImagePosZeroToOne = new Vector2(x / _captureResolution.width, 1 - y / _captureResolution.height);
            Vector2 ImagePosProjected2D = ((ImagePosZeroToOne * 2) - new Vector2(1, 1)); // -1 to 1 space
            Vector3 ImagePosProjected = new Vector3(ImagePosProjected2D.x, ImagePosProjected2D.y, 1);
            //UnityEngine.Debug.Log(ImagePosProjected);

            Vector3 CameraSpacePos = UnProjectVector(projectionMatrix, ImagePosProjected);
            //UnityEngine.Debug.Log(CameraSpacePos);
            CameraSpacePos.Normalize();
            CameraSpacePos *= depth;

            Vector3 WorldSpacePos = cameraToWorldMatrix.MultiplyPoint(CameraSpacePos);

            return WorldSpacePos;
        }

        public static Vector3 UnProjectVector(Matrix4x4 proj, Vector3 to)
        {
            /*
             * Source: https://developer.microsoft.com/en-us/windows/holographic/locatable_camera
             */
            Vector3 from = new Vector3(0, 0, 0);
            var axsX = proj.GetRow(0);
            var axsY = proj.GetRow(1);
            var axsZ = proj.GetRow(2);
            from.z = to.z / axsZ.z;
            from.y = (to.y - (from.z * axsY.z)) / axsY.y;
            from.x = (to.x - (from.z * axsX.z)) / axsX.x;
            return from;
        }

        #endregion Helper functions 
#endif
    }
}
