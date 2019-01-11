package edu.cmu.cs.gabrielclient.sensorstream;

import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;

import java.io.ByteArrayOutputStream;

import edu.cmu.cs.gabrielclient.network.StreamingThread;


public class VideoStream implements SensorStreamIF {
    private StreamingThread streamingThread;
    public Camera.PreviewCallback previewCallback = new Camera.PreviewCallback() {
        // called whenever a new frame is captured
        public void onPreviewFrame(byte[] frame, Camera mCamera) {
            if (streamingThread != null) {
                Camera.Parameters parameters = mCamera.getParameters();
                byte[] compressed = compress(frame, parameters);
                streamingThread.send(compressed);
            }
            mCamera.addCallbackBuffer(frame);
        }
    };

    public VideoStream(SensorStreamConfig config) {
        init(config);
    }

    @Override
    public void init(SensorStreamConfig config) {
        streamingThread = new StreamingThread(config);
    }

    @Override
    public void start() {
        streamingThread.start();
    }

    @Override
    public void stop() {
        if ((streamingThread != null) && (streamingThread.isAlive())) {
            streamingThread.stopStreaming();
            streamingThread = null;
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
