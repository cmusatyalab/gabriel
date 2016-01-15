package edu.cmu.cs.gabriel;

import java.io.File;

import android.os.Environment;

public class Const {
    /*
     * Experiment variable
     */

    public static final boolean IS_EXPERIMENT = false;
    public static final boolean PLAY_PRERECORDED = false;

    // Transfer from the file list
    // If TEST_IMAGE_DIR is not none, transmit from the image
    public static File ROOT_DIR = new File(Environment.getExternalStorageDirectory() + File.separator + "Gabriel" + File.separator);
    public static String app_name = "pool";
    //public static File TEST_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() + File.separator + "images-" + app_name + /* "-oneimage" + */ File.separator);
    public static File TEST_IMAGE_DIR = null;
    public static File COMPRESS_IMAGE_DIR = new File (ROOT_DIR.getAbsolutePath() + File.separator + "images-" + app_name + "-compress" + File.separator);

    // control VM
    public static String GABRIEL_IP = "128.2.213.106";  // Cloudlet
    //public static String GABRIEL_IP = "54.198.72.157";    // East
    //public static String GABRIEL_IP = "54.190.77.230";    // West
    //public static String GABRIEL_IP = "176.34.89.120";      // EU

    // Token
    public static int MAX_TOKEN_SIZE = 1;

    // image size and frame rate
    public static int MIN_FPS = 15;
    //Options: 320x180, 640x360, 1280x720, 1920x1080
    public static int IMAGE_WIDTH = 640;
    public static int IMAGE_HEIGHT = 320;

    // Result File
    public static String LATENCY_FILE_NAME = "latency-" + GABRIEL_IP + "-" + MAX_TOKEN_SIZE + ".txt";
    public static File LATENCY_DIR = new File(ROOT_DIR.getAbsolutePath() + File.separator + "exp");
    public static File LATENCY_FILE = new File (LATENCY_DIR.getAbsolutePath() + File.separator + LATENCY_FILE_NAME);
}
