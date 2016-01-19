package edu.cmu.cs.gabriel;

import java.io.File;

import android.os.Environment;

public class Const {
    // whether to do a demo or a set of experiments
    public static final boolean IS_EXPERIMENT = false;

    /************************ In both demo and experiment mode *******************/
    // directory for all application related files (input + output)
    public static final File ROOT_DIR = new File(Environment.getExternalStorageDirectory() + File.separator + "Gabriel" + File.separator);

    // image size and frame rate
    public static final int MIN_FPS = 15;
    // options: 320x180, 640x360, 1280x720, 1920x1080
    public static final int IMAGE_WIDTH = 640;
    public static final int IMAGE_HEIGHT = 360;
    
    // port protocol to the server
    public static final int VIDEO_STREAM_PORT = 9098;
    public static final int ACC_STREAM_PORT = 9099;
    public static final int RESULT_RECEIVING_PORT = 9101;

    /************************ Demo mode only *************************************/
    // server IP
    public static final String SERVER_IP = "128.2.213.106";  // Cloudlet

    // token size
    public static final int TOKEN_SIZE = 1;

    /************************ Experiment mode only *******************************/
    // server IP list
    public static final String[] SERVER_IP_LIST = {
            "128.2.213.106",
            };

    // token size list
    public static final int[] TOKEN_SIZE_LIST = {1};

    // load images (JPEG) from files and pretend they are just captured by the camera
    public static final String APP_NAME = "lego";
    public static final File TEST_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() + File.separator + "images-" + APP_NAME + File.separator);
    // a small number of images used for compression (bmp files), usually a subset of test images
    // these files are loaded into memory first so cannot have too many of them!
    public static final File COMPRESS_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() + File.separator + "images-" + APP_NAME + "-compress" + File.separator);

    // result file
    public static final File EXP_DIR = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp");
}