package edu.cmu.cs.gabrielclient.sensor;

import android.graphics.Bitmap;

import java.io.File;

public class Video {
    private static final String LOG_TAG = Video.class.getSimpleName();

    // image files for experiments (test and compression)
    private File[] imageFiles = null;
    private File[] imageFilesCompress = null;
    private Bitmap[] imageBitmapsCompress = new Bitmap[30];
    private int indexImageFile = 0;
    private int indexImageFileCompress = 0;
    private int imageFileCompressLength = -1;


    // frame data shared between threads
    private long frameID = 0;
    private long lastSentFrameID = 0;
    private byte[] frameBuffer = null;
    private Object frameLock = new Object();

}
