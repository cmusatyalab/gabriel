package edu.cmu.cs.gabriel;

import java.io.File;

import android.media.AudioFormat;
import android.os.Environment;

public class Const {
    public enum DeviceModel {
        GoogleGlass,
        Nexus6,
    }

    public static final DeviceModel deviceModel = DeviceModel.Nexus6;

    // whether to do a demo or a set of experiments
    public static final boolean IS_EXPERIMENT = false;

    // whether to use real-time captured images or load images from files for testing
    public static final boolean LOAD_IMAGES = false;
    // whether to use real-time sensed ACC values or load data from files for testing
    public static final boolean LOAD_ACC = false;
    // whether to use real-time captured audio or load audio data from files for testing
    public static final boolean LOAD_AUDIO = false;

    // high level sensor control (on/off)
    public static boolean SENSOR_VIDEO = true;
    public static boolean SENSOR_ACC = false;
    public static boolean SENSOR_AUDIO = false;

    public static String SYNC_BASE = "none";

    /************************ In both demo and experiment mode *******************/
    // directory for all application related files (input + output)
    public static final File ROOT_DIR = new File(Environment.getExternalStorageDirectory() +
            File.separator + "Gabriel" + File.separator);

    // image size and frame rate
    public static int CAPTURE_FPS = 15;
    // options: 320x180, 640x360, 1280x720, 1920x1080
    public static int IMAGE_WIDTH = 640;
    public static int IMAGE_HEIGHT = 360;
    
    // port protocol to the server
    public static final int VIDEO_STREAM_PORT = 9098;
    public static final int ACC_STREAM_PORT = 9099;
    public static final int AUDIO_STREAM_PORT = 9100;
    public static final int RESULT_RECEIVING_PORT = 9111;
    public static final int CONTROL_PORT = 22222;

    // the app name
    public static final String APP_NAME = "none";

    // load images (JPEG) from files and pretend they are just captured by the camera
    public static final File TEST_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() +
            File.separator + "images-" + APP_NAME + File.separator);

    // load audio data from file
    public static final File TEST_AUDIO_FILE = new File (ROOT_DIR.getAbsolutePath() +
            File.separator + "audio-" + APP_NAME + ".raw");

    // load acc data from file
    public static final File TEST_ACC_FILE = new File (ROOT_DIR.getAbsolutePath() +
            File.separator + "acc-" + APP_NAME + ".txt");

    // load acc to image timing mapping data from file
    public static final File IMAGE_TIMING_FILE = new File (TEST_IMAGE_DIR +
            File.separator + "timing.txt");

    // may include background pinging to keep network active
    public static final boolean BACKGROUND_PING = false;
    public static final int PING_INTERVAL = 20;

    // audio configurations
    public static final int RECORDER_SAMPLERATE = 16000;
    public static final int RECORDER_CHANNELS = AudioFormat.CHANNEL_IN_MONO;
    public static final int RECORDER_AUDIO_ENCODING = AudioFormat.ENCODING_PCM_16BIT;

    /************************ Demo mode only *************************************/
    // server IP
    public static final String SERVER_IP = "128.237.192.28";  // Cloudlet

    // token size
    public static final int TOKEN_SIZE = 1;

    /************************ Experiment mode only *******************************/
    // server IP list
    public static final String[] SERVER_IP_LIST = {
            "128.237.192.28",
            };

    // token size list
    public static final int[] TOKEN_SIZE_LIST = {1};

    // maximum times to ping (for time synchronization
    public static final int MAX_PING_TIMES = 20;

    // a small number of images used for compression (bmp files), usually a subset of test images
    // these files are loaded into memory first so cannot have too many of them!
    public static final File COMPRESS_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() +
            File.separator + "images-" + APP_NAME + "-compress" + File.separator);
    // the maximum allowed compress images to load
    public static final int MAX_COMPRESS_IMAGE = 3;
    
    // result file
    public static final File EXP_DIR = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp");

    // control log file
    public static final File CONTROL_LOG_FILE = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp" + File.separator + "control_log.txt");
}