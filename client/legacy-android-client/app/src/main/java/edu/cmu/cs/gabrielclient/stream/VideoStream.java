package edu.cmu.cs.gabrielclient.stream;

import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.util.Log;

import java.io.ByteArrayOutputStream;

import edu.cmu.cs.gabrielclient.network.ConnectionConfig;
import edu.cmu.cs.gabrielclient.network.RateLimitStreamingThread;
import edu.cmu.cs.gabrielclient.util.LifeCycleIF;


public class VideoStream implements LifeCycleIF {

    private static final String LOG_TAG = VideoStream.class.getSimpleName();
    private static RateLimitStreamingThread curNetworkThread;
    public static Camera.PreviewCallback previewCallback = new Camera.PreviewCallback() {
        // called whenever a new frame is captured
        public void onPreviewFrame(byte[] frame, Camera mCamera) {
            Log.v(LOG_TAG, "preview frame callback invoked");
            if (curNetworkThread != null) {
                Camera.Parameters parameters = mCamera.getParameters();
                byte[] compressed = compress(frame, parameters);
                curNetworkThread.send(compressed);
            }
            mCamera.addCallbackBuffer(frame);
        }
    };
    ConnectionConfig config;
    private RateLimitStreamingThread networkThread;

    public VideoStream(ConnectionConfig config) {
        this.config = config;
    }

    private static byte[] compress(byte[] data, Camera.Parameters parameters) {
        Camera.Size cameraImageSize = parameters.getPreviewSize();
        YuvImage image = new YuvImage(data, parameters.getPreviewFormat(), cameraImageSize.width,
                cameraImageSize.height, null);
        ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
        // chooses quality 67 and it roughly matches quality 5 in avconv
        image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()),
                67, tmpBuffer);
        return tmpBuffer.toByteArray();
    }

    @Override
    public void onResume() {
        networkThread = new RateLimitStreamingThread(this.config);
        networkThread.start();
        curNetworkThread = networkThread;
    }

    @Override
    public void onPause() {
        if ((networkThread != null) && (networkThread.isAlive())) {
            networkThread.close();
            networkThread = null;
        }
        curNetworkThread = null;
    }

    @Override
    public void onDestroy() {

    }
}
