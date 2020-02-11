package edu.cmu.cs.gabrielclient;

import java.io.File;

import android.hardware.Camera;
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

    public static boolean SHOW_SUBTITLES = false;

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
    public static String FOCUS_MODE = Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO;
    public static String FLASH_MODE = null;

    public static final int PORT = 9099;

    public static final String ENGINE_NAME = "instruction";

    // Controls whether the client should discard server's response if the engine update count
    // is the same. If the user state is tracked on the client side, this field is typically set
    // to be "true". If the user state is tracked on the server side, and the server will not
    // send duplicate instructions, then "false".
    // FSM-based cognitive engines generated with gabrieltool.statemachine set this field to "false"
    public static final boolean DEDUPLICATE_RESPONSE_BY_ENGINE_UPDATE_COUNT = true;

    // audio configurations
    public static final int RECORDER_SAMPLERATE = 16000;
    public static final int RECORDER_CHANNELS = AudioFormat.CHANNEL_IN_MONO;
    public static final int RECORDER_AUDIO_ENCODING = AudioFormat.ENCODING_PCM_16BIT;

    /************************ Demo mode only *************************************/
    // server IP
    public static String SERVER_IP = "";  // Cloudlet

    // token size
    public static final int TOKEN_SIZE = 1;

    // whether to save the camera feed as frame sequence
    public static final boolean SAVE_FRAME_SEQUENCE = false;
    public static final File SAVE_FRAME_SEQUENCE_DIR = new File (ROOT_DIR.getAbsolutePath() +
            File.separator + "saved-frame-sequence" + File.separator);

    /************************ Experiment mode only *******************************/
    // server IP list
    public static final String[] SERVER_IP_LIST = {
            "",
            };

    // token size list
    public static final int[] TOKEN_SIZE_LIST = {1};
    // result file
    public static final File EXP_DIR = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp");

    // control log file
    public static final File CONTROL_LOG_FILE = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp" + File.separator + "control_log.txt");
}