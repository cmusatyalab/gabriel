package edu.cmu.cs.gabriel;

import java.io.IOException;
import java.util.List;

import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.hardware.Camera.Size;
import android.util.AttributeSet;
import android.util.Log;
import android.view.SurfaceHolder;
import android.view.SurfaceView;

public class CameraPreview extends SurfaceView implements SurfaceHolder.Callback {
    private static final String LOG_TAG = "CameraPreview";

    private SurfaceHolder mHolder;
    private boolean isSurfaceReady = false;
    private boolean waitingToStart = false;
    private boolean isPreviewing = false;
    private Camera mCamera = null;
    private List<int[]> supportingFPS = null;
    private List<Camera.Size> supportingSize = null;

    public CameraPreview(Context context, AttributeSet attrs) {
        super(context, attrs);

        Log.v(LOG_TAG , "++CameraPreview");
        if (mCamera == null) {
            // Launching Camera App using voice command need to wait.
            // See more at https://code.google.com/p/google-glass-api/issues/list
            try {
                Thread.sleep(1000);
            } catch (InterruptedException e) {}
            mCamera = Camera.open();
        }

        mHolder = getHolder();
        mHolder.addCallback(this);
        mHolder.setType(SurfaceHolder.SURFACE_TYPE_PUSH_BUFFERS);
    }

    /**
     * This is only needed because once the application is onPaused, close() will be called, and during onResume,
     * the CameraPreview constructor is not called again.
     */
    public Camera checkCamera() {
        if (mCamera == null) {
            mCamera = Camera.open();
        }
        return mCamera;
    }

    public void start() {
        if (mCamera == null) {
            mCamera = Camera.open();
        }
        if (isSurfaceReady) {
            try {
                mCamera.setPreviewDisplay(mHolder);
            } catch (IOException exception) {
                Log.e(LOG_TAG, "Error in setting camera holder: " + exception.getMessage());
                this.close();
            }
            updateCameraConfigurations(Const.CAPTURE_FPS, Const.IMAGE_WIDTH, Const.IMAGE_HEIGHT, Const.FOCUS_MODE, Const.FLASH_MODE);
        } else {
            waitingToStart = true;
        }
    }

    public void close() {
        if (mCamera != null) {
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

    public void changeConfiguration(int[] range, Size imageSize, String focusMode, String flashMode) {
        Camera.Parameters parameters = mCamera.getParameters();
        if (range != null){
            Log.i("Config", "frame rate configuration : " + range[0] + "," + range[1]);
            parameters.setPreviewFpsRange(range[0], range[1]);
        }
        if (imageSize != null){
            Log.i("Config", "image size configuration : " + imageSize.width + "," + imageSize.height);
            parameters.setPreviewSize(imageSize.width, imageSize.height);
        }
        if (focusMode != null) {
            Log.i("Config", "focus mode configuration : " + focusMode);
            parameters.setFocusMode(focusMode);
        }
        if (flashMode != null){
            Log.i("Config", "flash mode configuration : " + flashMode);
            parameters.setFlashMode(flashMode);
        }
        mCamera.setParameters(parameters);
    }

    public void surfaceCreated(SurfaceHolder holder) {
        Log.d(LOG_TAG, "++surfaceCreated");
        isSurfaceReady = true;
        if (mCamera == null) {
            mCamera = Camera.open();
        }
        if (mCamera != null) {
            // get fps to capture
            Camera.Parameters parameters = mCamera.getParameters();
            this.supportingFPS = parameters.getSupportedPreviewFpsRange();
            for (int[] range: this.supportingFPS) {
                Log.i(LOG_TAG, "available fps ranges:" + range[0] + ", " + range[1]);
            }

            // get resolution
            this.supportingSize = parameters.getSupportedPreviewSizes();
            for (Camera.Size size: this.supportingSize) {
                Log.i(LOG_TAG, "available sizes:" + size.width + ", " + size.height);
            }

            if (waitingToStart) {
                waitingToStart = false;
                try {
                    mCamera.setPreviewDisplay(mHolder);
                } catch (IOException exception) {
                    Log.e(LOG_TAG, "Error in setting camera holder: " + exception.getMessage());
                    this.close();
                }
                updateCameraConfigurations(Const.CAPTURE_FPS, Const.IMAGE_WIDTH, Const.IMAGE_HEIGHT, Const.FOCUS_MODE, Const.FLASH_MODE);
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
        /*
         * Camera.Parameters parameters = mCamera.getParameters();
         * parameters.setPreviewSize(w, h); mCamera.setParameters(parameters);
         * mCamera.startPreview();
         */
    }

    public void updateCameraConfigurations(int targetFps, int imgWidth, int imgHeight, String focusMode, String flashMode) {
        if (mCamera != null) {
            if (targetFps != -1) {
                Const.CAPTURE_FPS = targetFps;
            }
            if (imgWidth != -1) {
                Const.IMAGE_WIDTH = imgWidth;
                Const.IMAGE_HEIGHT = imgHeight;
            }

            if (isPreviewing)
                mCamera.stopPreview();

            // set fps to capture
            int index = 0, fpsDiff = Integer.MAX_VALUE;
            for (int i = 0; i < this.supportingFPS.size(); i++){
                int[] frameRate = this.supportingFPS.get(i);
                int diff = Math.abs(Const.CAPTURE_FPS * 1000 - frameRate[0]) + Math.abs(Const.CAPTURE_FPS * 1000 - frameRate[1]);
                if (diff < fpsDiff){
                    fpsDiff = diff;
                    index = i;
                }
            }
            int[] targetRange = this.supportingFPS.get(index);

            // set resolution
            index = 0;
            int sizeDiff = Integer.MAX_VALUE;
            for (int i = 0; i < this.supportingSize.size(); i++){
                Camera.Size size = this.supportingSize.get(i);
                int diff = Math.abs(size.width - Const.IMAGE_WIDTH) + Math.abs(size.height - Const.IMAGE_HEIGHT);
                if (diff < sizeDiff){
                    sizeDiff = diff;
                    index = i;
                }
            }
            Camera.Size target_size = this.supportingSize.get(index);

            changeConfiguration(targetRange, target_size, focusMode, flashMode);

            mCamera.startPreview();
            isPreviewing = true;
        }
    }

}
