/**
* Copyright 2011 Carnegie Mellon University
*
* This material is being created with funding and support by the Department of Defense under Contract No. FA8721-05-C-0003 
* with Carnegie Mellon University for the operation of the Software Engineering Institute, a federally funded research and 
* development center.  As such, it is considered an externally sponsored project  under Carnegie Mellon University's 
* Intellectual Property Policy.
*
* This material may not be released outside of Carnegie Mellon University without first contacting permission@sei.cmu.edu.
*
* This material makes use of the following Third-Party Software and Libraries which are used pursuant to the referenced 
* Licenses.  Any modification of Third-Party Software or Libraries must be in compliance with the applicable license 
* (and only if permitted):
* 
*    Android
*    Source: http://source.android.com/source/index.html
*    License: http://source.android.com/source/licenses.html
* 
*    CherryPy
*    Source: http://cherrypy.org/
*    License: https://bitbucket.org/cherrypy/cherrypy/src/697c7af588b8/cherrypy/LICENSE.txt
*
* Unless otherwise stated in any Third-Party License or as otherwise required by applicable law or agreed to in writing, 
* All Third-Party Software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express 
* or implied.
*/

package edu.cmu.cs.cloudlet.android.application.face.ui;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.List;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.hardware.Camera.CameraInfo;
import android.hardware.Camera.PreviewCallback;
import android.hardware.Camera.Size;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.IBinder;
import android.os.Message;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Gravity;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.View.OnClickListener;
import android.view.ViewGroup;
import android.view.ViewGroup.LayoutParams;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Toast;
import edu.cmu.cs.cloudlet.android.R;
import edu.cmu.cs.cloudlet.android.application.face.network.FacerecIOService;
import edu.cmu.cs.cloudlet.android.application.face.network.IFacerecClientDataListener;
import edu.cmu.cs.cloudlet.android.application.face.network.IFacerecClientDataProvider;
import edu.cmu.cs.cloudlet.android.application.face.network.ImageResponseMessage;
import edu.cmu.cs.cloudlet.android.application.face.network.RawPreviewImageInfo;

// Need the following import to get access to the app resources, since this
// class is in a sub-package.
//import com.example.android.apis.R;

// ----------------------------------------------------------------------

public class FaceRecClientCameraPreview extends Activity implements
		OnClickListener {
	
	protected long timeStart, timeFirstResponse, timeFirstFinding;
	protected boolean isFirstFinding = false;
	protected boolean isFirstResponse = false;
	
	public void showInfoDialog(String message){
		new AlertDialog.Builder(FaceRecClientCameraPreview.this).setTitle("Info")
		.setMessage(message)
		.setIcon(R.drawable.ic_launcher)
		.setNegativeButton("Confirm", null)
		.show();	
	}

	class VideoOverlayView extends View {

		public static final String MODE_PREVIEW_TEXT = "Mode: Preview";
		public static final String MODE_TRAINING_TEXT = "Mode: Training";

		private Paint paint = new Paint();

		public VideoOverlayView(Context context) {
			super(context);

			paint.setStyle(Paint.Style.STROKE);
			paint.setColor(Color.RED);
		}

		DisplayMetrics mDisplayMetrics = new DisplayMetrics();

		/**
		 * Will be called everytime we call the invalidate method on this view.
		 */
		@Override
		protected void onDraw(Canvas canvas) {

			if (inTrainingMode)
				canvas.drawText(MODE_TRAINING_TEXT, 200, 30, paint);
			else
				canvas.drawText(MODE_PREVIEW_TEXT, 200, 30, paint);

			if (imageResponseMsg != null && imageResponseMsg.drawRect == 1) {

				getWindowManager().getDefaultDisplay().getMetrics(
						mDisplayMetrics);

				// The image preview window is smaller than the complete display
				// window.
				// The image we send to the server (on which it runs the facerec
				// algo and sends back the data) is
				// is the same size as the preview window size.

				// The canvas.draw() method uses the complete display window to
				// draw.
				// therefore we need to add an offset to the values that are
				// returned by the server.

				/**
				 * xOffset is the amount you need to add x which half the
				 * difference between the width of the actual display (that we
				 * get from the DiplayMetrics) and the width of the preview
				 * size.
				 */
				int xOffset = (mDisplayMetrics.widthPixels - mPreview.mPreviewSize.width) / 2;
				int yOffset = (mDisplayMetrics.heightPixels - mPreview.mPreviewSize.height) / 2;

				int left = imageResponseMsg.faceRect.x + xOffset;
				int top = imageResponseMsg.faceRect.y + yOffset;
				int right = imageResponseMsg.faceRect.x + xOffset
						+ imageResponseMsg.faceRect.width;
				int bottom = imageResponseMsg.faceRect.y + yOffset
						+ imageResponseMsg.faceRect.height;

				// draw the rect first
				Rect rect = new Rect(left, top, right, bottom);
				canvas.drawRect(rect, paint);
				
				// For Time stamping
				String message = "First runtime : ";
				if(isFirstFinding == false){
					timeFirstFinding = System.currentTimeMillis();
					isFirstFinding = true;

					message += "" + (timeFirstFinding - timeStart);
					Log.d(LOG_TAG, message);
					showInfoDialog(message);
				}

				canvas.drawText(message, 200, 100, paint);

				if (imageResponseMsg.name != null
						|| !imageResponseMsg.name.equalsIgnoreCase("")) {

					canvas.drawText(imageResponseMsg.name,
							imageResponseMsg.faceRect.x
									+ imageResponseMsg.faceRect.height + 20,
							imageResponseMsg.faceRect.y
									+ imageResponseMsg.faceRect.width + 20,
							paint);

					canvas.drawText(
							new Float(imageResponseMsg.confidence).toString(),
							imageResponseMsg.faceRect.x
									+ imageResponseMsg.faceRect.height + 20,
							imageResponseMsg.faceRect.y
									+ imageResponseMsg.faceRect.width + 30,
							paint);
				}

			}

			super.onDraw(canvas);
		}

	}

	private Preview mPreview;
	Camera mCamera;
	int numberOfCameras;
	int cameraCurrentlyLocked;

	// The first rear facing camera
	int defaultCameraId;

	protected WifiManager.WifiLock wifiLock;

	private String LOG_TAG = "FaceRecCameraPreview";

	private VideoOverlayView videoViewOverlay;
	private CheckBox trainingModeCheckbox;
	private EditText personNameTextEdit;
	private Button startTrainingButton;
	private boolean inTrainingMode;
	public static final int PREVIEW_MODE = 0;
	public static final int TRAINING_MODE = 1;

	private Handler dataThreadHandler;
	private ImageResponseMessage imageResponseMsg;

	protected IFacerecClientDataProvider serviceStub;

	private ServiceConnection serviceConnection = new ServiceConnection() {

		public void onServiceConnected(ComponentName className, IBinder binder) {

			Log.i(LOG_TAG,
					"Inside onServiceConnected() - Going to assign a binder to serviceStub. ");
			serviceStub = (IFacerecClientDataProvider) binder;

			try {
				serviceStub.registerActivityCallback(this.getClass().getName(),
						activityCallback);
			} catch (Throwable t) {
				t.printStackTrace();
			}

		}

		public void onServiceDisconnected(ComponentName className) {
			Log.i(LOG_TAG,
					"Inside onServiceDisconnected() - Going to assign a serviceStub to NULL ");
			serviceStub = null;
		}
	};

	private IFacerecClientDataListener activityCallback = new IFacerecClientDataListener() {

		// IMP: This is called from a NON-UI thread that is running in the
		// service. Therefore, we have to use a Handler.
		@Override
		public void updateImageResponseMessage(ImageResponseMessage response) {
			// get a new message from the handler
			Message handlerMsg = dataThreadHandler.obtainMessage();
			// set the response object
			handlerMsg.obj = response;
			
			
			// Time stamping
			if(isFirstResponse == false){
				timeFirstResponse = System.currentTimeMillis();
				isFirstResponse = true;				
				final String message = "Time for First Response\nstart: " + timeStart +"\nend:" + timeFirstResponse + "\ndiff: " + (timeFirstResponse- timeStart);
				Log.d(LOG_TAG, message);
				runOnUiThread(new Runnable() {
					public void run() {						
						showInfoDialog(message);
					}
				});

			}

			// send the message to the UI thread			
			dataThreadHandler.sendMessage(handlerMsg);
		}
	};

	private void doServiceBinding() {

		Intent serviceIntent = new Intent(this, FacerecIOService.class);

		// IMP - if you put extras in this intent they will not be visible to
		// the service onBind(Intent) method.
		boolean connected = bindService(serviceIntent, serviceConnection,
				BIND_AUTO_CREATE);

		if (connected) {
			Log.d(LOG_TAG,
					"FaceRecClientCameraPreview successfully bound to  Local FacerecIOService");
		} else {
			Log.e(LOG_TAG,
					"Error connecting FaceRecClientCameraPreview to the Local FacerecIOService.");
		}
	}

	protected String doWiFiStuff() {
		WifiManager wifiManager = (WifiManager) getSystemService(Context.WIFI_SERVICE);
		StringBuffer buf = new StringBuffer();
		WifiInfo info = wifiManager.getConnectionInfo();

		buf.append("SSID: ").append(info.getSSID()).append("\n");
		buf.append("Link Speed: ").append(info.getLinkSpeed()).append(" ")
				.append(WifiInfo.LINK_SPEED_UNITS).append("\n");
		Log.d(LOG_TAG,
				"WiFi Link speed " + info.getSSID() + " is: "
						+ info.getLinkSpeed() + " " + WifiInfo.LINK_SPEED_UNITS);
		Toast.makeText(this, "WiFi Link speed is: " + info.getLinkSpeed(),
				Toast.LENGTH_LONG).show();
		if (wifiManager.getWifiState() == WifiManager.WIFI_STATE_ENABLED) {
			buf.append("Connection State: ").append("WIFI_STATE_ENABLED")
					.append("\n");
		}

		// get a wifi lock.
		WifiManager.WifiLock wifiLock = wifiManager.createWifiLock(LOG_TAG);
		wifiLock.acquire();

		return buf.toString();
	}

	private void populateDisplayMetrics() {

		DisplayMetrics metrics = new DisplayMetrics();
		getWindowManager().getDefaultDisplay().getMetrics(metrics);
		Log.d(LOG_TAG, "Display metrics density (dots/inch) "
				+ covertDisplayDensityToString(metrics.densityDpi)
				+ " height (pixels) " + metrics.heightPixels
				+ " width (pixels)" + metrics.widthPixels);

	}

	private String covertDisplayDensityToString(int density) {
		switch (density) {
		case DisplayMetrics.DENSITY_XHIGH:
			return "DENSITY_XHIGH";

		case DisplayMetrics.DENSITY_HIGH:
			return "DENSITY_HIGH";

		case DisplayMetrics.DENSITY_MEDIUM:
			return "DENSITY_MEDIUM | DENSITY_DEFAULT";

		case DisplayMetrics.DENSITY_LOW:
			return "DENSITY_LOW";

		default:
			return "DENSITY_UNKNOWN";

		}

	}

	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);

		// Hide the window title.
		requestWindowFeature(Window.FEATURE_NO_TITLE);
		getWindow().addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);

		// Time stamping
		timeStart = System.currentTimeMillis();
		
		doServiceBinding();
		createDataCallbackHandler();

		// Create a RelativeLayout container that will hold a SurfaceView,
		// and set it as the content of our activity.
		mPreview = new Preview(this);
		setContentView(mPreview);

		videoViewOverlay = new VideoOverlayView(this);
		addContentView(videoViewOverlay, new LayoutParams(
				LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT));
		mPreview.setOverlayView(videoViewOverlay);

		addFaceRecUIControls();

		// Find the total number of cameras available
		numberOfCameras = Camera.getNumberOfCameras();

		// Find the ID of the default camera
		CameraInfo cameraInfo = new CameraInfo();
		for (int i = 0; i < numberOfCameras; i++) {
			Camera.getCameraInfo(i, cameraInfo);
			if (cameraInfo.facing == CameraInfo.CAMERA_FACING_BACK) {
				defaultCameraId = i;
			}
		}

	}

	private void createDataCallbackHandler() {

		this.dataThreadHandler = new Handler() {
			@Override
			public void handleMessage(Message msg) {
				ImageResponseMessage imageResponseMessage = (ImageResponseMessage) msg.obj;
				handleImageResponseonUI(imageResponseMessage);
				super.handleMessage(msg);
			}
		};
	}

	private void handleImageResponseonUI(ImageResponseMessage imgResMsg) {
		imageResponseMsg = imgResMsg;
		videoViewOverlay.invalidate();
	}

	private void addFaceRecUIControls() {

		trainingModeCheckbox = new CheckBox(this);
		trainingModeCheckbox.setChecked(false);
		trainingModeCheckbox.setOnClickListener(this);

		personNameTextEdit = new EditText(this);
		personNameTextEdit.setVisibility(EditText.INVISIBLE);

		startTrainingButton = new Button(this);
		startTrainingButton.setOnClickListener(this);
		startTrainingButton.setText("Start Training");
		startTrainingButton.setVisibility(View.INVISIBLE);

		LinearLayout layout = new LinearLayout(this);

		layout.addView(trainingModeCheckbox);
		layout.addView(personNameTextEdit);
		layout.addView(startTrainingButton);

		addContentView(layout, new LayoutParams(LayoutParams.WRAP_CONTENT,
				LayoutParams.WRAP_CONTENT));

	}

	@Override
	protected void onResume() {
		super.onResume();

		// Open the default i.e. the first rear facing camera.
		mCamera = Camera.open();
		cameraCurrentlyLocked = defaultCameraId;
		mPreview.setCamera(mCamera);
	}

	@Override
	protected void onPause() {
		super.onPause();

		// Because the Camera object is a shared resource, it's very
		// important to release it when the activity is paused.
		if (mCamera != null) {
			mCamera.stopPreview();
			mPreview.setCamera(null);
			mCamera.setPreviewCallback(null);
			mCamera.release();
			mCamera = null;
		}

		if (serviceConnection != null)
			unbindService(serviceConnection);

		if (wifiLock != null)
			wifiLock.release();
	}

	@Override
	public void onClick(View v) {

		if (v instanceof CheckBox) {
			if (trainingModeCheckbox.isChecked()) {
				Toast.makeText(this, "Going into training mode",
						Toast.LENGTH_SHORT).show();

				personNameTextEdit.setVisibility(EditText.VISIBLE);
				startTrainingButton.setVisibility(View.VISIBLE);

				videoViewOverlay.invalidate();

				return;

			} else {
				personNameTextEdit.setVisibility(EditText.INVISIBLE);
				personNameTextEdit.setEnabled(true);
				personNameTextEdit.setText("");
				personNameTextEdit.setHint("Enter Person Name");
				startTrainingButton.setVisibility(View.INVISIBLE);

				if (inTrainingMode) {
					serviceStub.stopTraining();
					inTrainingMode = false;

				}
			}
		} else if (startTrainingButton.getId() == v.getId()) {
			String personName = personNameTextEdit.getText().toString();

			// make sure that user has entered at least something
			if (personName.trim().length() > 0) {

				startTrainingButton.setVisibility(View.INVISIBLE);
				personNameTextEdit.setEnabled(false);

				// tell the serivce to send a training request with the name of
				// the person
				serviceStub.startTraining(personName);
				inTrainingMode = true;
			} else {
				Toast.makeText(this, "Please enter a valid person name",
						Toast.LENGTH_SHORT).show();
			}
		}
		videoViewOverlay.invalidate();
	}

	/**
	 * A simple wrapper around a Camera and a SurfaceView that renders a
	 * centered preview of the Camera to the surface. We need to center the
	 * SurfaceView because not all devices have cameras that support preview
	 * sizes at the same aspect ratio as the device's display.
	 */
	class Preview extends ViewGroup implements SurfaceHolder.Callback {
		private final String TAG = "Preview";

		SurfaceView mSurfaceView;
		SurfaceHolder mHolder;
		Size mPreviewSize;
		List<Size> mSupportedPreviewSizes;
		Camera mCamera;
		private VideoOverlayView overlayView;

		Preview(Context context) {
			super(context);

			mSurfaceView = new SurfaceView(context);
			addView(mSurfaceView);

			// Install a SurfaceHolder.Callback so we get notified when the
			// underlying surface is created and destroyed.
			mHolder = mSurfaceView.getHolder();
			mHolder.addCallback(this);
			mHolder.setType(SurfaceHolder.SURFACE_TYPE_PUSH_BUFFERS);
		}

		public void setOverlayView(VideoOverlayView view) {
			overlayView = view;
		}

		public void setCamera(Camera camera) {
			mCamera = camera;
			if (mCamera != null) {
				mSupportedPreviewSizes = mCamera.getParameters()
						.getSupportedPreviewSizes();
				requestLayout();
			}
		}

		public void switchCamera(Camera camera) {
			setCamera(camera);
			try {
				camera.setPreviewDisplay(mHolder);
			} catch (IOException exception) {
				Log.e(TAG, "IOException caused by setPreviewDisplay()",
						exception);
			}
			Camera.Parameters parameters = camera.getParameters();
			parameters.setPreviewSize(mPreviewSize.width, mPreviewSize.height);
			requestLayout();

			camera.setParameters(parameters);
		}

		@Override
		protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
			// We purposely disregard child measurements because act as a
			// wrapper to a SurfaceView that centers the camera preview instead
			// of stretching it.
			final int width = resolveSize(getSuggestedMinimumWidth(),
					widthMeasureSpec);
			final int height = resolveSize(getSuggestedMinimumHeight(),
					heightMeasureSpec);
			setMeasuredDimension(width, height);

			if (mSupportedPreviewSizes != null) {
				mPreviewSize = getOptimalPreviewSize(mSupportedPreviewSizes,
						width, height);
			}
		}

		@Override
		protected void onLayout(boolean changed, int l, int t, int r, int b) {
			if (changed && getChildCount() > 0) {
				final View child = getChildAt(0);

				final int width = r - l;
				final int height = b - t;

				int previewWidth = width;
				int previewHeight = height;
				if (mPreviewSize != null) {
					previewWidth = mPreviewSize.width;
					previewHeight = mPreviewSize.height;
				}

				// Center the child SurfaceView within the parent.
				if (width * previewHeight > height * previewWidth) {
					final int scaledChildWidth = previewWidth * height
							/ previewHeight;
					child.layout((width - scaledChildWidth) / 2, 0,
							(width + scaledChildWidth) / 2, height);
				} else {
					final int scaledChildHeight = previewHeight * width
							/ previewWidth;
					child.layout(0, (height - scaledChildHeight) / 2, width,
							(height + scaledChildHeight) / 2);
				}
			}
		}

		public void surfaceCreated(SurfaceHolder holder) {
			// The Surface has been created, acquire the camera and tell it
			// where
			// to draw.
			try {
				if (mCamera != null) {
					mCamera.setPreviewDisplay(holder);
				}
			} catch (IOException exception) {
				Log.e(TAG, "IOException caused by setPreviewDisplay()",
						exception);
			}
		}

		public void surfaceDestroyed(SurfaceHolder holder) {

		}

		private Size getOptimalPreviewSize(List<Size> sizes, int w, int h) {
			final double ASPECT_TOLERANCE = 0.1;
			double targetRatio = (double) w / h;
			if (sizes == null)
				return null;

			Size optimalSize = null;
			double minDiff = Double.MAX_VALUE;

			int targetHeight = h;

			// Try to find an size match aspect ratio and size
			for (Size size : sizes) {
				double ratio = (double) size.width / size.height;
				if (Math.abs(ratio - targetRatio) > ASPECT_TOLERANCE)
					continue;
				if (Math.abs(size.height - targetHeight) < minDiff) {
					optimalSize = size;
					minDiff = Math.abs(size.height - targetHeight);
				}
			}

			// Cannot find the one match the aspect ratio, ignore the
			// requirement
			if (optimalSize == null) {
				minDiff = Double.MAX_VALUE;
				for (Size size : sizes) {
					if (Math.abs(size.height - targetHeight) < minDiff) {
						optimalSize = size;
						minDiff = Math.abs(size.height - targetHeight);
					}
				}
			}
			return optimalSize;
		}

		public void surfaceChanged(SurfaceHolder holder, int format, int w,
				int h) {

			if (mCamera != null) {
				// Now that the size is known, set up the camera parameters and
				// begin
				// the preview.
				Camera.Parameters parameters = mCamera.getParameters();
				parameters.setPreviewSize(mPreviewSize.width,
						mPreviewSize.height);
				requestLayout();

				mCamera.setParameters(parameters);
				// add a preview call back that takes care receiving the actual
				// data
				mCamera.setPreviewCallback(previewCallback);
				mCamera.startPreview();
			}
		}

		PreviewCallback previewCallback = new PreviewCallback() {

			@Override
			public void onPreviewFrame(byte[] data, Camera camera) {

				RawPreviewImageInfo imageInfo = new RawPreviewImageInfo();
				imageInfo.imageData = data;
				imageInfo.height = mPreviewSize.height;
				imageInfo.width = mPreviewSize.width;

				// Log.e(LOG_TAG, "SOUMYA - preview height:" +
				// mPreviewSize.height + " and preview width " +
				// mPreviewSize.width );
				// testPrintDisplayMetrics();

				// testWriteDataAsJPGEtoSDCard(imageInfo);
				if (serviceStub != null)
					serviceStub.sendImageData(imageInfo);

			}

			int count = 0;

			private void testWriteDataAsJPGEtoSDCard(
					final RawPreviewImageInfo rawImageInfo) {
				if (count > 10)
					return;

				if (rawImageInfo == null)
					return;

				YuvImage yuvimage = new YuvImage(rawImageInfo.imageData,
						ImageFormat.NV21, rawImageInfo.width,
						rawImageInfo.height, null);
				ByteArrayOutputStream bos = new ByteArrayOutputStream();
				long start = System.currentTimeMillis();
				boolean result = yuvimage.compressToJpeg(new Rect(0, 0,
						rawImageInfo.width, rawImageInfo.height),
						FacerecIOService.JPEG_COMPRESSION_HINT, bos);
				long end = System.currentTimeMillis();

				if (result) {
					Log.e(LOG_TAG,
							"JPEG Image compression successful and took : ["
									+ (end - start) + "] ms.");
				} else {
					Log.e(LOG_TAG, "JPEG Image compression FAILED and took : ["
							+ (end - start) + "] ms.");

					return;
				}

				byte[] jpegImageBytes = bos.toByteArray();

				// write file to sdcard
				String filePath = Environment.getExternalStorageDirectory()
						+ "/cloudlet/" + count + "_preview_frame_2.jpeg";
				File file = new File(filePath);
				FileOutputStream fos;
				try {
					fos = new FileOutputStream(file);
					fos.write(jpegImageBytes);

					Log.e(LOG_TAG, "Successfully wrote " + filePath
							+ " to SD card.");

				} catch (FileNotFoundException e) {
					// TODO Auto-generated catch block
					e.printStackTrace();
				} catch (IOException ioe) {
					ioe.printStackTrace();
				}

				count++;

			}
		};

	}
}

// ----------------------------------------------------------------------

