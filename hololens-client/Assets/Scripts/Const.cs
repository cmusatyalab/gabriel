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
        
        // load images (JPEG) from files and pretend they are just captured by the camera
        public static string APP_NAME = "lego";
        public static string TEST_IMAGE_DIR_NAME = "images-" + APP_NAME;

        /************************ Demo mode only *************************************/
        // server
        public static string SERVER_IP = "128.2.213.130";  // Cloudlet
        public static int PORT = 9099;

        public static string ENGINE_NAME = "instruction";

        // whether to capture holograms for display (only capture for half of the time)
        public static bool HOLO_CAPTURE = true;
    }
}
#endif