package edu.cmu.cs.elijah.native_ocr;

import java.io.IOException;
import java.util.List;

import android.app.Activity;
import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.util.Log;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;

public class CameraPreview extends SurfaceView implements SurfaceHolder.Callback {
	private static final String LOG_TAG = "CameraPreview";
	
    private SurfaceHolder mHolder;
    private Camera mCamera;
    
    private Activity pActivity;     // need to reference parent activity when setting orientation
    
    CameraPreview(Context context) {
        super(context);

        // Install a SurfaceHolder.Callback so we get notified when the
        // underlying surface is created and destroyed.
        mHolder = getHolder();
        mHolder.addCallback(this);
        // deprecated setting, but required on Android versions prior to 3.0
        // mHolder.setType(SurfaceHolder.SURFACE_TYPE_PUSH_BUFFERS);
        
        pActivity = (Activity) context;     // Zhuo: don't know if this is legal...
    }
    
    public void setCamera(Camera camera) {
        if (mCamera == camera)
            return;
        if (mCamera != null) {
            mCamera.stopPreview();
            mCamera.release();
        }
        mCamera = camera;
        setupCamera();
    }
    
    private void setupCamera() {
        Camera.Parameters parameters = mCamera.getParameters();        

        // set fps to capture
        List<int[]> supportedFps = parameters.getSupportedPreviewFpsRange();
        for (int[] range: supportedFps) {
            Log.v(LOG_TAG, "available fps ranges:" + range[0] + ", " + range[1]);
        }
        int[] targetRange = supportedFps.get(2);	// 15
        Log.d(LOG_TAG, "Selected fps:" + targetRange[0] + ", " + targetRange[1]);
        parameters.setPreviewFpsRange(targetRange[0], targetRange[1]);
        
        // set resolusion
        List<Camera.Size> supportedSizes = parameters.getSupportedPreviewSizes();
        for (Camera.Size size: supportedSizes) {
            Log.v(LOG_TAG, "available sizes:" + size.width + ", " + size.height);
        }
        Camera.Size targetSize = supportedSizes.get( supportedSizes.size()/2 );
        Log.d(LOG_TAG, "Selected size:" + 320 + ", " + 240);
        parameters.setPreviewSize(320, 240);
        
        // set picture format
        parameters.setPictureFormat(ImageFormat.JPEG);
        
        // set focus mode
        List<String> focusModes = parameters.getSupportedFocusModes();
        if (focusModes.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO))
        {
            parameters.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_VIDEO);
            Log.d(LOG_TAG, "set cameara focus to continuous_video");
        }
        
        mCamera.setParameters(parameters);
    }
    
    public void setPreviewCallback(PreviewCallback previewCallback) {
        Log.v(LOG_TAG, "Setting preview callback");
        mCamera.setPreviewCallback(previewCallback);
    }
    
    public void surfaceCreated(SurfaceHolder holder) {
        // The Surface has been created, now tell the camera where to draw the preview.
        try {
            mCamera.setPreviewDisplay(holder);
            mCamera.startPreview();
            Log.i(LOG_TAG, "preview started");
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error setting camera preview: " + e.getMessage());
        }
        
        setCameraDisplayOrientation(pActivity, 0, mCamera);
    }

    public void surfaceDestroyed(SurfaceHolder holder) {
        mCamera.stopPreview();
        //mCamera.release();
    }

    public void surfaceChanged(SurfaceHolder holder, int format, int w, int h) {
        // If your preview can change or rotate, take care of those events here.
        // Make sure to stop the preview before resizing or reformatting it.

        if (mHolder.getSurface() == null){ return; }

        // stop preview before making changes
        try {
            mCamera.stopPreview();
        } catch (Exception e){
            Log.d(LOG_TAG, "Tried to stop a non-existent preview: " + e.getMessage());
        }

        // set preview size and make any resize, rotate or reformatting changes here
        setCameraDisplayOrientation(pActivity, 0, mCamera);

        // start preview with new settings
        try {
            mCamera.setPreviewDisplay(mHolder);
            mCamera.startPreview();
        } catch (Exception e){
            Log.e(LOG_TAG, "Error starting camera preview: " + e.getMessage());
        }
    }
    
    // Zhuo: I can't really see the effect of this function...
    private static void setCameraDisplayOrientation(Activity activity, int cameraId, Camera camera) {
        Camera.CameraInfo info = new Camera.CameraInfo();
        Camera.getCameraInfo(cameraId, info);
        int rotation = activity.getWindowManager().getDefaultDisplay().getRotation();
        int degrees = 0;
        switch (rotation) {
            case Surface.ROTATION_0: degrees = 0; break;
            case Surface.ROTATION_90: degrees = 90; break;
            case Surface.ROTATION_180: degrees = 180; break;
            case Surface.ROTATION_270: degrees = 270; break;
        }

        int result = (info.orientation - degrees + 360) % 360;
        camera.setDisplayOrientation(result);
    }
}
