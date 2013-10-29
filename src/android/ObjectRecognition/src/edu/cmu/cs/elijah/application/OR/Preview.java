package edu.cmu.cs.elijah.application.OR;

import java.io.IOException;
import java.util.List;

import android.content.Context;
import android.hardware.Camera;
import android.hardware.Camera.Size;
import android.util.AttributeSet;
import android.util.Log;
import android.view.SurfaceHolder;
import android.view.SurfaceView;

public class Preview extends SurfaceView implements SurfaceHolder.Callback {

	public static final int FULLIMAGE_SIZE_WIDTH = 480; // smallest size in droid
	public static final int THUMBNAIL_SIZE_WIDTH = 320; // smallest size in droid
	public static final int THUMBNAIL_QUALITY = 70;
	public static final int FULLIMAGE_QUALITY = 90;

	public SurfaceHolder mHolder;
	public Camera mCamera = null;

	public void close() {
		if (mCamera != null) {
			mCamera.stopPreview();
			mCamera.release();
			mCamera = null;

		}
	}

	public Preview(Context context, AttributeSet attrs) {
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
		}
		if (mCamera != null) {
			try {
				mCamera.setPreviewDisplay(holder);
				Camera.Parameters parameters = mCamera.getParameters();
				// thumbnail size
				Size thumbnailSize = parameters.getJpegThumbnailSize();
				parameters.setJpegThumbnailSize(THUMBNAIL_SIZE_WIDTH, thumbnailSize.height * THUMBNAIL_SIZE_WIDTH / thumbnailSize.width);
				parameters.setJpegThumbnailQuality(THUMBNAIL_QUALITY);
				mCamera.setParameters(parameters);

				Camera.Parameters newParameters = mCamera.getParameters();
				Size newSize = newParameters.getJpegThumbnailSize();
				Log.d("krha", "thumbnail size : (" + thumbnailSize.width + ", " + thumbnailSize.height + ") --> " + +newSize.width + ", " + newSize.height
						+ ")");

				// full image size
				parameters = mCamera.getParameters();
				Size closeSize = null;
				int difference = Integer.MAX_VALUE;
				List<Camera.Size> list = parameters.getSupportedPictureSizes();
				for (Camera.Size size : list) {
					if (size.width < FULLIMAGE_SIZE_WIDTH)
						continue;
					if (difference > Math.abs((size.width - FULLIMAGE_SIZE_WIDTH))) {
						difference = Math.abs((size.width - FULLIMAGE_SIZE_WIDTH));
						closeSize = size;
					}
					 Log.d("krha", "supporting fullimage size : " + size.width + " " + size.height);
				}
				parameters.setPictureSize(closeSize.width, closeSize.height);
				parameters.setJpegQuality(FULLIMAGE_QUALITY);
				mCamera.setParameters(parameters);

				newSize = mCamera.getParameters().getPictureSize();
				Log.d("krha", "FullImage size : (" + newSize.width + ", " + newSize.height + ")");

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

		Camera.Parameters parameters = mCamera.getParameters();
		parameters.setPreviewSize(w, h);
//		mCamera.setParameters(parameters);
		mCamera.startPreview();
	}

}
