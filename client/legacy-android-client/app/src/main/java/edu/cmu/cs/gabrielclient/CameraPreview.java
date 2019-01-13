package edu.cmu.cs.gabrielclient;

import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.hardware.Camera.Size;
import android.util.AttributeSet;
import android.util.Log;
import android.view.SurfaceHolder;
import android.view.SurfaceView;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

public class CameraPreview extends SurfaceView implements SurfaceHolder.Callback {
    private static final String LOG_TAG = "CameraPreview";
    public byte[] reusedFrameBuffer = null;
    private SurfaceHolder mHolder;
    private boolean isSurfaceReady = false;
    private boolean waitingToStart = false;
    // store startCamera parameters temporarily since java doesn't have partial function
    // capabilities
    private List<Object> waitingToStartSetup = new ArrayList<Object>();
    private boolean isPreviewing = false;
    private Camera mCamera = null;

    public CameraPreview(Context context, AttributeSet attrs) {
        super(context, attrs);

        Log.v(LOG_TAG, "++CameraPreview");
        if (mCamera == null) {
            // Launching Camera App using voice command need to wait.
            // See more at https://code.google.com/p/google-glass-api/issues/list
            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {
            }
            mCamera = Camera.open();
        }

        mHolder = getHolder();
        mHolder.addCallback(this);
        mHolder.setType(SurfaceHolder.SURFACE_TYPE_PUSH_BUFFERS);
    }

    /**
     * This is only needed because once the application is onPaused, close() will be called, and
     * during onResume,
     * the CameraPreview constructor is not called again.
     */
    public Camera checkCamera() {
        if (mCamera == null) {
            mCamera = Camera.open();
        }
        return mCamera;
    }

    /**
     * Create reused frame buffer for preview and set preview callback.
     * The camera settings need to be updated using updateConfigurations before calling this method.
     *
     * @param cb
     */
    public void setPreviewBufferAndSetPreviewCallback(PreviewCallback cb) {
        Camera cam = checkCamera();
        Camera.Parameters parameters = cam.getParameters();
        int previewFormat = parameters.getPreviewFormat();
        int previewBitsPerPixel = ImageFormat.getBitsPerPixel(previewFormat);
        cam.setPreviewCallbackWithBuffer(cb);
        Size sz = parameters.getPreviewSize();
        this.reusedFrameBuffer = new byte[(int) Math.ceil((double) (sz.height * sz.width *
                previewBitsPerPixel / 8))]; // 1.5 bytes per pixel
        cam.addCallbackBuffer(this.reusedFrameBuffer);
        Log.d(LOG_TAG, "created and set preview buffer for current preview format (" +
                previewFormat + "), which has " + previewBitsPerPixel + " bits per pixel");
        Log.d(LOG_TAG, "added preview callback: " + cb.toString());
    }

    private void startCamera(CameraConfiguration camConfig, PreviewCallback cb) {
        if (mCamera == null) {
            mCamera = Camera.open();
        }
        try {
            mCamera.setPreviewDisplay(mHolder);
        } catch (IOException exception) {
            Log.e(LOG_TAG, "Error in setting camera holder: " + exception.getMessage());
            this.close();
        }
        updateCameraConfigurations(camConfig);
        setPreviewBufferAndSetPreviewCallback(cb);
    }

    public void start(CameraConfiguration camConfig, PreviewCallback cb) {
        if (isSurfaceReady) {
            startCamera(camConfig, cb);
        } else {
            waitingToStartSetup.clear();
            waitingToStartSetup.add(camConfig);
            waitingToStartSetup.add(cb);
            waitingToStart = true;
        }
    }

    public void close() {
        if (mCamera != null) {
            mCamera.setPreviewCallback(null);
            mCamera.stopPreview();
            isPreviewing = false;
            mCamera.release();
            mCamera = null;
        }
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        // TODO Auto-generated method stub
        super.onMeasure(widthMeasureSpec, heightMeasureSpec);
    }

    public void changeConfiguration(int[] range, Size imageSize, String focusMode, String
            flashMode) {
        Camera.Parameters parameters = mCamera.getParameters();
        if (range != null) {
            Log.i("Config", "frame rate configuration : " + range[0] + "," + range[1]);
            parameters.setPreviewFpsRange(range[0], range[1]);
        }
        if (imageSize != null) {
            Log.i("Config", "image size configuration : " + imageSize.width + "," + imageSize
                    .height);
            parameters.setPreviewSize(imageSize.width, imageSize.height);
        }
        if (focusMode != null) {
            Log.i("Config", "focus mode configuration : " + focusMode);
            parameters.setFocusMode(focusMode);
        }
        if (flashMode != null) {
            Log.i("Config", "flash mode configuration : " + flashMode);
            parameters.setFlashMode(flashMode);
        }
        mCamera.setParameters(parameters);
    }

    private void printSupportedCameraConfiguration(Camera cam) {
        // get fps to capture
        Camera.Parameters parameters = cam.getParameters();
        for (int[] range : parameters.getSupportedPreviewFpsRange()) {
            Log.i(LOG_TAG, "available fps ranges:" + range[0] + ", " + range[1]);
        }
        // get resolution
        for (Camera.Size size : parameters.getSupportedPreviewSizes()) {
            Log.i(LOG_TAG, "available sizes:" + size.width + ", " + size.height);
        }
        // get focusMode
        for (String focusMode : parameters.getSupportedFocusModes()) {
            Log.i(LOG_TAG, "available focus mode:" + focusMode);
        }
        // get resolution
        for (String flashMode : parameters.getSupportedFlashModes()) {
            Log.i(LOG_TAG, "available flash mode:" + flashMode);
        }
    }

    public void surfaceCreated(SurfaceHolder holder) {
        Log.d(LOG_TAG, "++surfaceCreated");
        isSurfaceReady = true;
        if (mCamera == null) {
            mCamera = Camera.open();
        }
        if (mCamera != null) {
            printSupportedCameraConfiguration(mCamera);
            if (waitingToStart) {
                waitingToStart = false;
                startCamera((CameraConfiguration) waitingToStartSetup.get(0), (PreviewCallback)
                        waitingToStartSetup.get(1));
                waitingToStartSetup.clear();
            }
        } else {
            Log.w(LOG_TAG, "Camera is not open");
        }
    }

    public void surfaceDestroyed(SurfaceHolder holder) {
        isSurfaceReady = false;
        this.close();
    }

    public void surfaceChanged(SurfaceHolder holder, int format, int w, int h) {
        Log.d(LOG_TAG, "surface changed");
    }

    /**
     * Find and set the best HW supported configurations for the target the configuration.
     *
     * @param camConfig
     */
    public void updateCameraConfigurations(CameraConfiguration camConfig) {
        if (mCamera != null) {
            if (isPreviewing)
                mCamera.stopPreview();

            Camera.Parameters parameters = mCamera.getParameters();
            List<int[]> supportingFpsRange = parameters.getSupportedPreviewFpsRange();
            // find best match fps
            int index = 0, fpsDiff = Integer.MAX_VALUE;
            for (int i = 0; i < supportingFpsRange.size(); i++) {
                int[] frameRate = supportingFpsRange.get(i);
                int diff = Math.abs(camConfig.fps * 1000 - frameRate[0]) + Math.abs(camConfig.fps
                        * 1000 - frameRate[1]);
                if (diff < fpsDiff) {
                    fpsDiff = diff;
                    index = i;
                }
            }
            int[] targetRange = supportingFpsRange.get(index);

            // find best match resolution
            List<Camera.Size> supportingSize = parameters.getSupportedPictureSizes();
            index = 0;
            int sizeDiff = Integer.MAX_VALUE;
            for (int i = 0; i < supportingSize.size(); i++) {
                Camera.Size size = supportingSize.get(i);
                int diff = Math.abs(size.width - camConfig.imgWidth) + Math.abs(size.height -
                        camConfig.imgHeight);
                if (diff < sizeDiff) {
                    sizeDiff = diff;
                    index = i;
                }
            }
            Camera.Size target_size = supportingSize.get(index);

            // choose the 1st return focusMode if cannot find the target mode
            List<String> supportingFocusMode = parameters.getSupportedFocusModes();
            if (!supportingFocusMode.contains(camConfig.focusMode)) {
                camConfig.focusMode = supportingFocusMode.get(0);
            }

            // choose the 1st returned flashMode if cannot find the target mode
            List<String> supportingFlashMode = parameters.getSupportedFlashModes();
            if (supportingFlashMode == null || supportingFlashMode.isEmpty()) {
                camConfig.flashMode = null;
            } else {
                if (!supportingFlashMode.contains(camConfig.flashMode)) {
                    camConfig.flashMode = supportingFlashMode.get(0);
                }
            }

            changeConfiguration(targetRange, target_size, camConfig.focusMode, camConfig.flashMode);

            mCamera.startPreview();
            isPreviewing = true;
        }
    }

    /**
     * Camera Configuration Settings. Different from Camera.Parameters, it allows null values.
     * If a setting is null, hw camera's default value is used.
     * This is a user's target configuration, best-efforts are made to select the closest
     * settings supported by h/w.
     * Singleton class to retain configurations by default.
     */
    public static class CameraConfiguration {
        private static CameraConfiguration camConfig = new CameraConfiguration(Const.CAPTURE_FPS,
                Const.IMAGE_WIDTH, Const.IMAGE_HEIGHT, Const.FOCUS_MODE, Const.FLASH_MODE);
        public int fps;
        public int imgWidth;
        public int imgHeight;
        public String focusMode;
        public String flashMode;

        private CameraConfiguration(int fps, int imgWidth, int imgHeight, String focusMode,
                                    String flashMode) {
            this.fps = fps;
            this.imgWidth = imgWidth;
            this.imgHeight = imgHeight;
            this.focusMode = focusMode;
            this.flashMode = flashMode;
        }

        public static CameraConfiguration getInstance() {
            return camConfig;
        }
    }

}
