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

    public SurfaceHolder mHolder;
    public Camera mCamera = null;
    public List<int[]> supportingFPS = null;
    public List<Camera.Size> supportingSize = null;

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

    public void close() {
        if (mCamera != null) {
            mCamera.stopPreview();
            mCamera.release();
            mCamera = null;
        }
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        // TODO Auto-generated method stub
        super.onMeasure(widthMeasureSpec, heightMeasureSpec);
    }

    public void changeConfiguration(int[] range, Size imageSize) {
        Camera.Parameters parameters = mCamera.getParameters();
        if (range != null){
            Log.i("Config", "frame rate configuration : " + range[0] + "," + range[1]);
            parameters.setPreviewFpsRange(range[0], range[1]);
        }
        if (imageSize != null){
            Log.i("Config", "image size configuration : " + imageSize.width + "," + imageSize.height);
            parameters.setPreviewSize(imageSize.width, imageSize.height);
        }
        //parameters.setPreviewFormat(ImageFormat.JPEG);

        mCamera.setParameters(parameters);
    }

    public void surfaceCreated(SurfaceHolder holder) {
        Log.d(LOG_TAG, "++surfaceCreated");
        if (mCamera == null) {
            mCamera = Camera.open();
            // mCamera.setDisplayOrientation(90);
        }
        if (mCamera != null) {
            try {
                mCamera.setPreviewDisplay(holder);
                // set fps to capture
                Camera.Parameters parameters = mCamera.getParameters();
                List<int[]> supportedFps = parameters.getSupportedPreviewFpsRange();
                for (int[] range: supportedFps) {
                    Log.i(LOG_TAG, "available fps ranges:" + range[0] + ", " + range[1]);
                }
                if(this.supportingFPS == null)
                    this.supportingFPS = supportedFps;
                int index = 0, fpsDiff = Integer.MAX_VALUE;
                for (int i = 0; i < supportedFps.size(); i++){
                    int[] frameRate = supportedFps.get(i);
                    int diff = Math.abs(Const.CAPTURE_FPS * 1000 - frameRate[0]);
                    if (diff < fpsDiff){
                        fpsDiff = diff;
                        index = i;
                    }
                }
                int[] targetRange = supportedFps.get(index);

                // set resolution
                List<Camera.Size> supportedSizes = parameters.getSupportedPreviewSizes();
                for (Camera.Size size: supportedSizes) {
                    Log.i(LOG_TAG, "available sizes:" + size.width + ", " + size.height);
                }
                if(this.supportingSize == null)
                    this.supportingSize = supportedSizes;
                index = 0;
                int sizeDiff = Integer.MAX_VALUE;
                for (int i = 0; i < supportedSizes.size(); i++){
                    Camera.Size size = supportedSizes.get(i);
                    int diff = Math.abs(size.width - Const.IMAGE_WIDTH) + Math.abs(size.height - Const.IMAGE_HEIGHT);
                    if (diff < sizeDiff){
                        sizeDiff = diff;
                        index = i;
                    }
                }
                Camera.Size target_size = supportedSizes.get(index);
//              List<Integer> supportedFormat = parameters.getSupportedPreviewFormats();

                changeConfiguration(targetRange, target_size);
                mCamera.startPreview();

            } catch (IOException exception) {
                Log.e("Error", "exception:surfaceCreated Camera Open ");
                this.close();
            }
        }
    }

    public void surfaceDestroyed(SurfaceHolder holder) {
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

    public void setPreviewCallback(PreviewCallback previewCallback) {
        if (this.mCamera != null){
            mCamera.setPreviewCallback(previewCallback);
        }
    }

    public void setPreviewCallbackWithBuffer(PreviewCallback previewCallback) {
        if (this.mCamera != null){
            mCamera.setPreviewCallbackWithBuffer(previewCallback);
        }
    }

    public void addCallbackBuffer(byte[] buffer) {
        if (this.mCamera != null) {
            mCamera.addCallbackBuffer(buffer);
        }
    }

    public Camera getCamera() {
        return mCamera;
    }

    public void updateCameraConfigurations(int targetFps, int imgWidth, int imgHeight) {
        if (mCamera != null) {
            if (targetFps != -1) {
                Const.CAPTURE_FPS = targetFps;
            }
            if (imgWidth != -1) {
                Const.IMAGE_WIDTH = imgWidth;
                Const.IMAGE_HEIGHT = imgHeight;
            }

            mCamera.stopPreview();
            // set fps to capture
            int index = 0, fpsDiff = Integer.MAX_VALUE;
            for (int i = 0; i < this.supportingFPS.size(); i++){
                int[] frameRate = this.supportingFPS.get(i);
                int diff = Math.abs(Const.CAPTURE_FPS * 1000 - frameRate[0]);
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

            changeConfiguration(targetRange, target_size);

            mCamera.startPreview();
        }
    }

}
