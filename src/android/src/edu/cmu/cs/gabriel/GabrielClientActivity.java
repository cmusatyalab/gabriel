package edu.cmu.cs.gabriel;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.Locale;
import java.util.Timer;
import java.util.TimerTask;

import edu.cmu.cs.gabriel.R;
import edu.cmu.cs.gabriel.network.NetworkProtocol;
import edu.cmu.cs.gabriel.network.VideoControlThread;
import edu.cmu.cs.gabriel.network.VideoStreamingThread;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.hardware.SensorManager;
import android.hardware.Camera.PreviewCallback;
import android.nfc.Tag;
import android.opengl.GLSurfaceView;
import android.os.Bundle;
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

public class GabrielClientActivity extends Activity implements TextToSpeech.OnInitListener {
	private static final String LOG_TAG = "krha";

	private static final int SETTINGS_ID = Menu.FIRST;
	private static final int EXIT_ID = SETTINGS_ID + 1;
	private static final int CHANGE_SETTING_CODE = 2;
	private static final int CAPTURE_VIDEO_ACTIVITY_REQUEST_CODE = 200;
	private static final int LOCAL_OUTPUT_BUFF_SIZE = 1024 * 100;

	public String REMOTE_IP = "128.2.210.197";
	public static int REMOTE_CONTROL_PORT = 5000;
	public static int REMOTE_DATA_PORT = 9098;

	CameraConnector cameraRecorder;
	VideoStreamingThread videoSenderThread;

	private SharedPreferences sharedPref;
	private boolean hasStarted;
	private TextView statView;
	private CameraPreview mPreview;
	private BufferedOutputStream localOutputStream;

	protected TextToSpeech mTTS = null;

	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        init();
        startBatteryRecording();
	}

	private void init() {
		mPreview = (CameraPreview) findViewById(R.id.camera_preview);
		
		// TextToSpeech.OnInitListener
		if (mTTS == null){
			mTTS = new TextToSpeech(this, this);			
		}
		cameraRecorder = null;
		videoSenderThread = null;
		hasStarted = false;
		localOutputStream = null;
		
		startCapture();
	}

	// Implements TextToSpeech.OnInitListener
	public void onInit(int status) {
		if (status == TextToSpeech.SUCCESS) {
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
				videoSenderThread.push(frame, parameters);
			}
		}
	};

	private Handler returnMsgHandler = new Handler() {
		public void handleMessage(Message msg) {
			
			if (msg.what == NetworkProtocol.NETWORK_RET_FAILED) {
				Bundle data = msg.getData();
				String message = data.getString("message");
				stopStreaming();
				new AlertDialog.Builder(GabrielClientActivity.this).setTitle("INFO").setMessage(message)
						.setIcon(R.drawable.ic_launcher).setNegativeButton("Confirm", null).show();
			} 
			if (msg.what == NetworkProtocol.NETWORK_RET_RESULT) {
				String ttsMessage = (String) msg.obj;

				// Select a random hello.
				Log.d(LOG_TAG, "tts string origin: " + ttsMessage);
				mTTS.setSpeechRate(1f);
				mTTS.speak(ttsMessage, TextToSpeech.QUEUE_FLUSH, null);
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
	
	private void startCapture(){
		if (hasStarted == false) {
			Log.i(LOG_TAG, "StreamingThread starts");
			mPreview.setPreviewCallback(previewCallback);

			if (cameraRecorder == null) {
				cameraRecorder = new CameraConnector();
				cameraRecorder.init();
			}
			if (videoSenderThread == null) {
				videoSenderThread = new VideoStreamingThread(cameraRecorder.getOutputFileDescriptor(), REMOTE_IP, REMOTE_DATA_PORT, returnMsgHandler);
				videoSenderThread.start();
			}
			localOutputStream = new BufferedOutputStream(new FileOutputStream(
					cameraRecorder.getInputFileDescriptor()), LOCAL_OUTPUT_BUFF_SIZE);
			
			hasStarted = true;
		}
	}
	
	public void startStreaming(View view) throws IOException {
		startCapture();
	}

	public void stopStreaming() {
		hasStarted = false;
		if (mPreview != null)
			mPreview.setPreviewCallback(null);
		if (videoSenderThread != null && videoSenderThread.isAlive()) {
			videoSenderThread.stopStreaming();
		}
	}

	@Override
	protected void onActivityResult(int requestCode, int resultCode, Intent data) {

		switch (requestCode) {
		case CAPTURE_VIDEO_ACTIVITY_REQUEST_CODE:
			Toast.makeText(this, "Video saved to:\n" + data.getData(), Toast.LENGTH_LONG).show();
			break;
		case CHANGE_SETTING_CODE:
			getPreferences();
			break;
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

		REMOTE_IP = sharedPref.getString(SettingsActivity.KEY_PROXY_IP, "128.2.213.25");
		REMOTE_CONTROL_PORT = Integer.parseInt(sharedPref.getString(SettingsActivity.KEY_PROXY_PORT, "8080"));

		Log.v("pref", "remotePort is" + REMOTE_CONTROL_PORT);
	}
	
	/*
	 * Recording battery info by sending an intent
	 * Current and voltage at the time
	 * Sample every 100ms
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
		if (cameraRecorder != null) {
			cameraRecorder.close();
			cameraRecorder = null;
		}
		if ((videoSenderThread != null) && (videoSenderThread.isAlive())) {
			videoSenderThread.stopStreaming();
			videoSenderThread = null;
		}
		if (mPreview != null) {
			mPreview.setPreviewCallback(null);
			mPreview.close();
			mPreview = null;
		}
		finish();
	}	
}
