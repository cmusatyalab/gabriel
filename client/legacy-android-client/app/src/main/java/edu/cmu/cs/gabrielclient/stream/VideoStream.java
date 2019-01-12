package edu.cmu.cs.gabrielclient.stream;

import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;

import java.io.ByteArrayOutputStream;

import edu.cmu.cs.gabrielclient.network.RateLimitStreamingThread;


public class VideoStream implements StreamIF {

    private static final String LOG_TAG = RateLimitStreamingThread.class.getSimpleName();
    private RateLimitStreamingThread rateLimitStreamingThread;
    public Camera.PreviewCallback previewCallback = new Camera.PreviewCallback() {
        // called whenever a new frame is captured
        public void onPreviewFrame(byte[] frame, Camera mCamera) {
            if (rateLimitStreamingThread != null) {
                Camera.Parameters parameters = mCamera.getParameters();
                byte[] compressed = compress(frame, parameters);
                rateLimitStreamingThread.send(compressed);
            }
            mCamera.addCallbackBuffer(frame);
        }
    };

    public VideoStream(StreamConfig config) {
        init(config);
    }

    @Override
    public void init(StreamConfig config) {
        rateLimitStreamingThread = new RateLimitStreamingThread(config);
    }

    @Override
    public void start() {
        rateLimitStreamingThread.start();
    }

    @Override
    public void stop() {
        if ((rateLimitStreamingThread != null) && (rateLimitStreamingThread.isAlive())) {
            rateLimitStreamingThread.close();
            rateLimitStreamingThread = null;
        }
    }

    private byte[] compress(byte[] data, Camera.Parameters parameters) {
        Camera.Size cameraImageSize = parameters.getPreviewSize();
        YuvImage image = new YuvImage(data, parameters.getPreviewFormat(), cameraImageSize.width,
                cameraImageSize.height, null);
        ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
        // chooses quality 67 and it roughly matches quality 5 in avconv
        image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 67, tmpBuffer);
        return tmpBuffer.toByteArray();
    }
}
