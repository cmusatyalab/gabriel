#if !UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace gabriel_client
{
    class Const
    {
        // whether to do a demo or a set of experiments
        public static bool IS_EXPERIMENT = false;

        // whether to use real-time captured images or load images from files for testing
        public static bool LOAD_IMAGES = false;

        /************************ In both demo and experiment mode *******************/
        // directory for all application related files (input + output)
        public static Windows.Storage.StorageFolder ROOT_DIR = Windows.Storage.ApplicationData.Current.LocalFolder;

        // image size and frame rate
        public static int MIN_FPS = 15;
        // options: 320x180, 640x360, 1280x720, 1920x1080
        public static int IMAGE_WIDTH = 640;
        public static int IMAGE_HEIGHT = 360;

        // port protocol to the server
        public static int VIDEO_STREAM_PORT = 9098;
        public static int ACC_STREAM_PORT = 9099;
        public static int RESULT_RECEIVING_PORT = 9111;
        public static int CONTROL_PORT = 22222;

        // load images (JPEG) from files and pretend they are just captured by the camera
        public static string APP_NAME = "lego";
        public static string TEST_IMAGE_DIR_NAME = "images-" + APP_NAME;

        /************************ Demo mode only *************************************/
        // server IP
        public static string SERVER_IP = "128.2.213.106";  // Cloudlet

        // token size
        public static int TOKEN_SIZE = 1;

        // whether to capture holograms for display (only capture for half of the time)
        public static bool HOLO_CAPTURE = true;

        /************************ Experiment mode only *******************************/
        // server IP list
        public static string[] SERVER_IP_LIST = {
            "128.2.213.106",
            };

        // token size list
        public static int[] TOKEN_SIZE_LIST = { 1 };

        // maximum times to ping (for time synchronization
        public static int MAX_PING_TIMES = 20;

        // a small number of images used for compression (bmp files), usually a subset of test images
        // these files are loaded into memory first so cannot have too many of them!
        public static string COMPRESS_IMAGE_DIR_NAME = "images-" + APP_NAME + "-compress";
        // the maximum allowed compress images to load
        public static int MAX_COMPRESS_IMAGE = 3;

        // result file
        public static string EXP_DIR_NAME = "exp";
    }
}
#endif