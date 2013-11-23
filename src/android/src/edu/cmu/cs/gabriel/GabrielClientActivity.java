package edu.cmu.cs.gabriel;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.Locale;
import java.util.Timer;
import java.util.TimerTask;

import edu.cmu.cs.gabriel.R;
import edu.cmu.cs.gabriel.network.AccStreamingThread;
import edu.cmu.cs.gabriel.network.NetworkProtocol;
import edu.cmu.cs.gabriel.network.ResultReceivingThread;
import edu.cmu.cs.gabriel.network.VideoStreamingThread;
import edu.cmu.cs.gabriel.token.TokenController;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.Camera.PreviewCallback;
import android.nfc.Tag;
import android.opengl.GLSurfaceView;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Message;
import android.preference.PreferenceManager;
import android.speech.tts.TextToSpeech;
import android.util.Log;
import android.view.Display;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;
import android.widget.Toast;

public class GabrielClientActivity extends Activity implements TextToSpeech.OnInitListener, SensorEventListener {
	
	private static final String LOG_TAG = "krha";

	private static final int SETTINGS_ID = Menu.FIRST;
	private static final int EXIT_ID = SETTINGS_ID + 1;
	private static final int CHANGE_SETTING_CODE = 2;
	private static final int LOCAL_OUTPUT_BUFF_SIZE = 1024 * 100;

	public static final int VIDEO_STREAM_PORT = 9098;
	public static final int ACC_STREAM_PORT = 9099;
	public static final int GPS_PORT = 9100;
	public static final int RESULT_RECEIVING_PORT = 9101;

	CameraConnector cameraRecorder;
	ControlThread controlThread;
	
	VideoStreamingThread videoStreamingThread;
	AccStreamingThread accStreamingThread;
	ResultReceivingThread resultThread;
	TokenController tokenController = null;

	private SharedPreferences sharedPref;
	private boolean hasStarted;
	private TextView statView;
	private CameraPreview mPreview;
	private BufferedOutputStream localOutputStream;
	AlertDialog errorAlertDialog;

	private SensorManager mSensorManager = null;
	private Sensor mAccelerometer = null;
	protected TextToSpeech mTTS = null;
	
	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		setContentView(R.layout.activity_main);
		getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED+
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON+
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
		
		init();
		startBatteryRecording();
	}

	private void init() {
		mPreview = (CameraPreview) findViewById(R.id.camera_preview);
		Const.ROOT_DIR.mkdirs();

		if (tokenController == null){
			tokenController = new TokenController(Const.LATENCY_FILE);
		}
		if (mSensorManager == null) {
			mSensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
			mAccelerometer = mSensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
			mSensorManager.registerListener(this, mAccelerometer, SensorManager.SENSOR_DELAY_NORMAL);
		}

		// TextToSpeech.OnInitListener
		if (mTTS == null) {
			mTTS = new TextToSpeech(this, this);
		}
		cameraRecorder = null;
		videoStreamingThread = null;
		accStreamingThread = null;
		resultThread = null;
		controlThread = null;
		hasStarted = false;
		localOutputStream = null;

		if (this.errorAlertDialog == null) {
			this.errorAlertDialog = new AlertDialog.Builder(GabrielClientActivity.this).create();
			this.errorAlertDialog.setTitle("Error");
			this.errorAlertDialog.setIcon(R.drawable.ic_launcher);
		}

		startCapture();
	}

	// Implements TextToSpeech.OnInitListener
	public void onInit(int status) {
		if (status == TextToSpeech.SUCCESS) {
			if (mTTS == null){
				mTTS = new TextToSpeech(this, this);
			}
			int result = mTTS.setLanguage(Locale.US);
			if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
				Log.e("krha_app", "Language is not available.");
			}
		} else {
			// Initialization failed.
			Log.e("krha_app", "Could not initialize TextToSpeech.");
		}
	}

	@Override
	protected void onResume() {
		super.onResume();
		this.init();
	}

	@Override
	protected void onPause() {
		super.onPause();
		this.terminate();
	}

	@Override
	protected void onDestroy() {
		stopBatteryRecording();
		this.terminate();
		super.onDestroy();
	}

	@Override
	public boolean onCreateOptionsMenu(Menu menu) {
		// Inflate the menu; this adds items to the action bar if it is present.
		getMenuInflater().inflate(R.menu.main, menu);
		menu.add(0, SETTINGS_ID, 0, "Settings").setIcon(android.R.drawable.ic_menu_preferences);
		menu.add(0, EXIT_ID, 1, "Exit").setIcon(android.R.drawable.ic_menu_close_clear_cancel);
		return true;
	}

	@Override
	public boolean onOptionsItemSelected(MenuItem item) {
		Intent intent;

		switch (item.getItemId()) {
		case SETTINGS_ID:
			intent = new Intent().setClass(this, SettingsActivity.class);
			startActivityForResult(intent, CHANGE_SETTING_CODE);
			break;
		case EXIT_ID:
			finish();
			break;
		}

		return super.onOptionsItemSelected(item);
	}

	private PreviewCallback previewCallback = new PreviewCallback() {

		public void onPreviewFrame(byte[] frame, Camera mCamera) {
			if (hasStarted && (localOutputStream != null)) {
				Camera.Parameters parameters = mCamera.getParameters();
				videoStreamingThread.push(frame, parameters);
			}
		}
	};

	private Handler returnMsgHandler = new Handler() {
		public void handleMessage(Message msg) {
			if (msg.what == NetworkProtocol.NETWORK_RET_FAILED) {
				Bundle data = msg.getData();
				String message = data.getString("message");
				stopStreaming();
//				new AlertDialog.Builder(GabrielClientActivity.this).setTitle("INFO").setMessage(message)
//						.setIcon(R.drawable.ic_launcher).setNegativeButton("Confirm", null).show();
			}
			if (msg.what == NetworkProtocol.NETWORK_RET_RESULT) {
				String ttsMessage = (String) msg.obj;

				// Select a random hello.
				Log.d(LOG_TAG, "tts string origin: " + ttsMessage);
				mTTS.setSpeechRate(1f);
				mTTS.speak(ttsMessage, TextToSpeech.QUEUE_FLUSH, null);

				// Bundle data = msg.getData();
				// double avgfps = data.getDouble("avg_fps");
				// double fps = data.getDouble("fps");
				// if (fps != 0.0) {
				// Log.e("krha", fps + " " + avgfps);
				// statView.setText("FPS - " + String.format("%04.2f", fps) +
				// "(current), "
				// + String.format("%04.2f", avgfps) + "(avg)");
				// }
			}
		}
	};

	private Handler controlMsgHandler = new Handler() {
		public void handleMessage(Message msg_in) {
			if (msg_in.what == ControlThread.CODE_TCP_SETUP_SUCCESS) {
				Handler handler = controlThread.getHandler();
				// now ask the control channel to query streaming port
				Message msg_out = Message.obtain();
				Bundle data = new Bundle();
				data.putString("serviceName", "streaming");
				data.putString("resourceName", "video");
				msg_out.what = ControlThread.CODE_QUERY_PORT;
				msg_out.setData(data);
				handler.sendMessage(msg_out);
			} else if (msg_in.what == ControlThread.CODE_TCP_SETUP_FAIL) {
				// nothing for now
			} else if (msg_in.what == ControlThread.CODE_STREAM_PORT) {
				int serverStreamPort = msg_in.arg1;
				if (cameraRecorder == null) {
					cameraRecorder = new CameraConnector();
					cameraRecorder.init();
				}
				if (resultThread == null) {
					resultThread = new ResultReceivingThread(Const.GABRIEL_IP, RESULT_RECEIVING_PORT, returnMsgHandler, tokenController);
					resultThread.start();
				}
				if (videoStreamingThread == null) {
					videoStreamingThread = new VideoStreamingThread(cameraRecorder.getOutputFileDescriptor(),
							Const.GABRIEL_IP, VIDEO_STREAM_PORT, returnMsgHandler, tokenController);
					videoStreamingThread.start();
				}
				if (accStreamingThread == null) {
					accStreamingThread = new AccStreamingThread(Const.GABRIEL_IP, ACC_STREAM_PORT, returnMsgHandler, tokenController);
					accStreamingThread.start();
				}

				localOutputStream = new BufferedOutputStream(new FileOutputStream(
						cameraRecorder.getInputFileDescriptor()), LOCAL_OUTPUT_BUFF_SIZE);
				hasStarted = true;
			}
		}
	};

	protected int selectedRangeIndex = 0;

	public void selectFrameRate(View view) throws IOException {
		selectedRangeIndex = 0;
		final List<int[]> rangeList = this.mPreview.supportingFPS;
		String[] rangeListString = new String[rangeList.size()];
		for (int i = 0; i < rangeListString.length; i++) {
			int[] targetRange = rangeList.get(i);
			rangeListString[i] = new String(targetRange[0] + " ~" + targetRange[1]);
		}

		AlertDialog.Builder ab = new AlertDialog.Builder(this);
		ab.setTitle("FPS Range List");
		ab.setIcon(R.drawable.ic_launcher);
		ab.setSingleChoiceItems(rangeListString, 0, new DialogInterface.OnClickListener() {
			public void onClick(DialogInterface dialog, int position) {
				selectedRangeIndex = position;
			}
		}).setPositiveButton("Ok", new DialogInterface.OnClickListener() {
			public void onClick(DialogInterface dialog, int position) {
				if (position >= 0) {
					selectedRangeIndex = position;
				}
				int[] targetRange = rangeList.get(selectedRangeIndex);
				mPreview.changeConfiguration(targetRange, null);
			}
		}).setNegativeButton("Cancel", new DialogInterface.OnClickListener() {
			public void onClick(DialogInterface dialog, int position) {
				return;
			}
		});
		ab.show();
	}

	protected int selectedSizeIndex = 0;

	public void selectImageSize(View view) throws IOException {
		selectedSizeIndex = 0;
		final List<Camera.Size> imageSize = this.mPreview.supportingSize;
		String[] sizeListString = new String[imageSize.size()];
		for (int i = 0; i < sizeListString.length; i++) {
			Camera.Size targetRange = imageSize.get(i);
			sizeListString[i] = new String(targetRange.width + " ~" + targetRange.height);
		}

		AlertDialog.Builder ab = new AlertDialog.Builder(this);
		ab.setTitle("Image Size List");
		ab.setIcon(R.drawable.ic_launcher);
		ab.setSingleChoiceItems(sizeListString, 0, new DialogInterface.OnClickListener() {
			public void onClick(DialogInterface dialog, int position) {
				selectedRangeIndex = position;
			}
		}).setPositiveButton("Ok", new DialogInterface.OnClickListener() {
			public void onClick(DialogInterface dialog, int position) {
				if (position >= 0) {
					selectedRangeIndex = position;
				}
				Camera.Size targetSize = imageSize.get(selectedRangeIndex);
				mPreview.changeConfiguration(null, targetSize);
			}
		}).setNegativeButton("Cancel", new DialogInterface.OnClickListener() {
			public void onClick(DialogInterface dialog, int position) {
				return;
			}
		});
		ab.show();
	}

	private void startCapture() {
		if (hasStarted == false) {
			mPreview.setPreviewCallback(previewCallback);
			controlThread = new ControlThread(controlMsgHandler, Const.GABRIEL_IP);
			controlThread.start();
		}
	}

	public void startStreaming(View view) throws IOException {
		startCapture();
	}

	public void stopStreaming() {
		hasStarted = false;
		if (mPreview != null)
			mPreview.setPreviewCallback(null);
		if (videoStreamingThread != null && videoStreamingThread.isAlive()) {
			videoStreamingThread.stopStreaming();
		}
		if (accStreamingThread != null && accStreamingThread.isAlive()) {
			accStreamingThread.stopStreaming();
		}
		if (resultThread != null && resultThread.isAlive()) {
			resultThread.close();
		}
	}

	public void setDefaultPreferences() {
		// setDefaultValues will only be invoked if it has not been invoked
		PreferenceManager.setDefaultValues(this, R.xml.preferences, false);
		sharedPref = PreferenceManager.getDefaultSharedPreferences(this);

		sharedPref.edit().putBoolean(SettingsActivity.KEY_PROXY_ENABLED, true);
		sharedPref.edit().putString(SettingsActivity.KEY_PROTOCOL_LIST, "UDP");
		sharedPref.edit().putString(SettingsActivity.KEY_PROXY_IP, "128.2.213.25");
		sharedPref.edit().putInt(SettingsActivity.KEY_PROXY_PORT, 8080);
		sharedPref.edit().commit();

	}

	public void getPreferences() {
		sharedPref = PreferenceManager.getDefaultSharedPreferences(this);
		String sProtocol = sharedPref.getString(SettingsActivity.KEY_PROTOCOL_LIST, "UDP");
		String[] sProtocolList = getResources().getStringArray(R.array.protocol_list);
	}

	/*
	 * Recording battery info by sending an intent Current and voltage at the
	 * time Sample every 100ms
	 */
	Intent batteryRecordingService = null;

	public void startBatteryRecording() {
		BatteryRecordingService.AppName = "GabrielClient";
		Log.i("wenluh", "Starting Battery Recording Service");
		batteryRecordingService = new Intent(this, BatteryRecordingService.class);
		startService(batteryRecordingService);
	}

	public void stopBatteryRecording() {
		Log.i("wenluh", "Stopping Battery Recording Service");
		if (batteryRecordingService != null) {
			stopService(batteryRecordingService);
			batteryRecordingService = null;
		}
	}

	@Override
	public boolean onKeyDown(int keyCode, KeyEvent event) {
		if (keyCode == KeyEvent.KEYCODE_BACK) {
			this.terminate();
			return true;
		}

		return super.onKeyDown(keyCode, event);
	}

	private void terminate() {
		// Don't forget to shutdown!
		if (mTTS != null) {
			Log.d(LOG_TAG, "TTS is closed");
			mTTS.stop();
			mTTS.shutdown();
			mTTS = null;
		}
		if (controlThread != null) {
			Message msg_out = Message.obtain();
			msg_out.what = ControlThread.CODE_CLOSE_CONNECTION;
			Handler controlHandler = controlThread.getHandler();
			controlHandler.sendMessage(msg_out);
			controlThread = null;
		}
		if (cameraRecorder != null) {
			cameraRecorder.close();
			cameraRecorder = null;
		}
		if ((resultThread != null) && (resultThread.isAlive())) {
			resultThread.close();
			resultThread = null;
		}
		if ((videoStreamingThread != null) && (videoStreamingThread.isAlive())) {
			videoStreamingThread.stopStreaming();
			videoStreamingThread = null;
		}
		if ((accStreamingThread != null) && (accStreamingThread.isAlive())) {
			accStreamingThread.stopStreaming();
			accStreamingThread = null;
		}
		if (mPreview != null) {
			mPreview.setPreviewCallback(null);
			mPreview.close();
			mPreview = null;
		}
		if (mSensorManager != null) {
			mSensorManager.unregisterListener(this);
			mSensorManager = null;
			mAccelerometer = null;
		}
		if (tokenController != null){
			tokenController.close();
		}
		finish();
	}

	@Override
	public void onAccuracyChanged(Sensor sensor, int accuracy) {
	}

	@Override
	public void onSensorChanged(SensorEvent event) {
		if (event.sensor.getType() != Sensor.TYPE_ACCELEROMETER)
			return;
		if (accStreamingThread != null) {
//			accStreamingThread.push(event.values);
		}
		// Log.d(LOG_TAG, "acc_x : " + mSensorX + "\tacc_y : " + mSensorY);
	}
}
