package edu.cmu.cs.gabriel;

import java.io.IOException;
import java.util.List;

import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.util.AttributeSet;
import android.util.Log;
import android.view.SurfaceHolder;
import android.view.SurfaceView;

public class CameraPreview extends SurfaceView implements SurfaceHolder.Callback {

	public static final int IMAGE_WIDTH = 320;
	public static final int IMAGE_HEIGHT = 240;

	public SurfaceHolder mHolder;
	public Camera mCamera = null;

	public void close() {
		if (mCamera != null) {
			mCamera.stopPreview();
			mCamera.release();
			mCamera = null;
		}
	}

	public CameraPreview(Context context, AttributeSet attrs) {
		super(context, attrs);
		Log.d("krha", "context : " + context);
		if (mCamera == null) {
			mCamera = Camera.open();
		}

		mHolder = getHolder();
		mHolder.addCallback(this);
		mHolder.setType(SurfaceHolder.SURFACE_TYPE_PUSH_BUFFERS);
	}

	@Override
	protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
		// TODO Auto-generated method stub
		super.onMeasure(widthMeasureSpec, heightMeasureSpec);
	}

	public void surfaceCreated(SurfaceHolder holder) {

		if (mCamera == null) {
			mCamera = Camera.open();
//	        mCamera.setDisplayOrientation(90);
		}
		if (mCamera != null) {
			try {
				mCamera.setPreviewDisplay(holder);
		        Camera.Parameters parameters = mCamera.getParameters();
		        // set fps to capture
		        List<int[]> supportedFps = parameters.getSupportedPreviewFpsRange();
		        int[] targetRange = supportedFps.get(supportedFps.size() - 1);
		        parameters.setPreviewFpsRange(targetRange[0], targetRange[1]);
//		        parameters.setPreviewFpsRange(targetRange[0], FPS_RANGE);
		        // set resolusion
		        List<Camera.Size> supportedSizes = parameters.getSupportedPreviewSizes();
		        parameters.setPreviewSize(IMAGE_WIDTH, IMAGE_HEIGHT);        
		        parameters.setPictureFormat(ImageFormat.JPEG);
//		        parameters.set("orientation", "portrait");
		        
		        mCamera.setParameters(parameters);
				mCamera.startPreview();

			} catch (IOException exception) {
				Log.e("Error", "exception:surfaceCreated Camera Open ");
				mCamera.release();
				mCamera = null;
				// TODO: add more exception handling logic here
			}
		}
	}

	public void surfaceDestroyed(SurfaceHolder holder) {
		if (mCamera != null) {
			mCamera.stopPreview();
			mCamera.release();
			mCamera = null;
		}
	}

	public void surfaceChanged(SurfaceHolder holder, int format, int w, int h) {
		/*
		Camera.Parameters parameters = mCamera.getParameters();
		parameters.setPreviewSize(w, h);
		mCamera.setParameters(parameters);
		mCamera.startPreview();
		*/
	}

    public void setPreviewCallback(PreviewCallback previewCallback) {
        mCamera.setPreviewCallback(previewCallback);
    }
    
	public Camera getCamera() {
		return mCamera;
	}

}
