package edu.cmu.cs.gabriel;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
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
import android.hardware.Camera;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.Camera.PreviewCallback;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.preference.PreferenceManager;
import android.speech.tts.TextToSpeech;
import android.util.Log;
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
	
	VideoStreamingThread videoStreamingThread;
	AccStreamingThread accStreamingThread;
	ResultReceivingThread resultThread;
	TokenController tokenController = null;

	private SharedPreferences sharedPref;
	private boolean hasStarted;
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
	
		// one time run
//		init_once();
//		init_experiement();
		
		// scriptized experiement
		runExperiements();
	}
	
	protected void runExperiements(){
		final Timer startTimer = new Timer();
		TimerTask autoStart = new TimerTask(){
			String[] ipList = {"128.2.213.102"};
//			int[] tokenSize = {1, 4, 16, 32, 64};
			int[] tokenSize = {10000};
			int ipIndex = 0;
			int tokenIndex = 0;			
			@Override
			public void run() {
				GabrielClientActivity.this.runOnUiThread(new Runnable() {
		            @Override
		            public void run() {
						// end condition
						if ((ipIndex == ipList.length) || (tokenIndex == tokenSize.length)) {
							Log.d(LOG_TAG, "Finish all experiemets");
							startTimer.cancel();
							return;
						}
						
						// make a new configuration
						Const.GABRIEL_IP = ipList[ipIndex];
						Const.MAX_TOKEN_SIZE = tokenSize[tokenIndex];
						Const.LATENCY_FILE_NAME = "latency-" + Const.GABRIEL_IP + "-" + Const.MAX_TOKEN_SIZE + ".txt";
						Const.LATENCY_FILE = new File (Const.ROOT_DIR.getAbsolutePath() +
								File.separator + "exp" +
								File.separator + Const.LATENCY_FILE_NAME);
						Log.d(LOG_TAG, "Start new experiemet");
						Log.d(LOG_TAG, "ip: " + Const.GABRIEL_IP +"\tToken: " + Const.MAX_TOKEN_SIZE);

						
						// run the experiment
						init_experiement();
						
						// move on the next experiment
						tokenIndex++;
						if (tokenIndex == tokenSize.length){
							tokenIndex = 0;
							ipIndex++;
						}
		            }
		        });
			}
		};
		
		// run 3 minutes for each experiement
		init_once();
		startTimer.schedule(autoStart, 1000, 5*60*1000);
	}

	private void init_once() {
		mPreview = (CameraPreview) findViewById(R.id.camera_preview);
		mPreview.setPreviewCallback(previewCallback);
		Const.ROOT_DIR.mkdirs();
		Const.LATENCY_DIR.mkdirs();
		// TextToSpeech.OnInitListener
		if (mTTS == null) {
//			mTTS = new TextToSpeech(this, this);
		}
		if (mSensorManager == null) {
			mSensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
			mAccelerometer = mSensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
			mSensorManager.registerListener(this, mAccelerometer, SensorManager.SENSOR_DELAY_NORMAL);
		}
		if (this.errorAlertDialog == null) {
			this.errorAlertDialog = new AlertDialog.Builder(GabrielClientActivity.this).create();
			this.errorAlertDialog.setTitle("Error");
			this.errorAlertDialog.setIcon(R.drawable.ic_launcher);
		}
		if (cameraRecorder != null) {
			cameraRecorder.close();
			cameraRecorder = null;
		}
		if (localOutputStream != null){
			try {
				localOutputStream.close();
			} catch (IOException e) {}
			localOutputStream = null;
		}
		
		if (cameraRecorder == null) {
			cameraRecorder = new CameraConnector();
			cameraRecorder.init();
			Log.d(LOG_TAG, "new cameraRecorder");
		}
		if (localOutputStream == null){
			localOutputStream = new BufferedOutputStream(new FileOutputStream(
					cameraRecorder.getInputFileDescriptor()), LOCAL_OUTPUT_BUFF_SIZE);

			Log.d(LOG_TAG, "new localoutputStream");
		}
		hasStarted = true;
	}
	
	private void init_experiement() {
		startBatteryRecording();
		if (tokenController != null){
			tokenController.close();
		}
		if ((videoStreamingThread != null) && (videoStreamingThread.isAlive())) {
			videoStreamingThread.stopStreaming();
			videoStreamingThread = null;
		}
		if ((accStreamingThread != null) && (accStreamingThread.isAlive())) {
			accStreamingThread.stopStreaming();
			accStreamingThread = null;
		}
		if ((resultThread != null) && (resultThread.isAlive())) {
			resultThread.close();
			resultThread = null;
		}
		
		try {
			Thread.sleep(5*1000);
		} catch (InterruptedException e) {}
		
		tokenController = new TokenController(Const.LATENCY_FILE);
		resultThread = new ResultReceivingThread(Const.GABRIEL_IP, RESULT_RECEIVING_PORT, returnMsgHandler, tokenController);
		resultThread.start();
		
		FileDescriptor fd = cameraRecorder.getOutputFileDescriptor();
		videoStreamingThread = new VideoStreamingThread(fd,
				Const.GABRIEL_IP, VIDEO_STREAM_PORT, returnMsgHandler, tokenController);
		videoStreamingThread.start();
		
		accStreamingThread = new AccStreamingThread(Const.GABRIEL_IP, ACC_STREAM_PORT, returnMsgHandler, tokenController);
		accStreamingThread.start();		
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
	}

	@Override
	protected void onPause() {
		super.onPause();
		this.terminate();
		finish();
	}

	@Override
	protected void onDestroy() {
		this.terminate();
		super.onDestroy();
		finish();
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
				if (videoStreamingThread != null){
//					Log.d(LOG_TAG, "in");
					videoStreamingThread.push(frame, parameters);					
				}
			}
		}
	};

	private Handler returnMsgHandler = new Handler() {
		public void handleMessage(Message msg) {
			if (msg.what == NetworkProtocol.NETWORK_RET_FAILED) {
				Bundle data = msg.getData();
				String message = data.getString("message");
//				stopStreaming();
//				new AlertDialog.Builder(GabrielClientActivity.this).setTitle("INFO").setMessage(message)
//						.setIcon(R.drawable.ic_launcher).setNegativeButton("Confirm", null).show();
			}
			if (msg.what == NetworkProtocol.NETWORK_RET_RESULT) {
				if (mTTS != null){
					String ttsMessage = (String) msg.obj;

					// Select a random hello.
					Log.d(LOG_TAG, "tts string origin: " + ttsMessage);
					mTTS.setSpeechRate(1f);
					mTTS.speak(ttsMessage, TextToSpeech.QUEUE_FLUSH, null);					
				}
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
	
	private void terminate() {
		// change only soft state
		stopBatteryRecording();
		
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
		if (tokenController != null){
			tokenController.close();
			tokenController = null;
		}
		
		// Don't forget to shutdown!
		if (mTTS != null) {
			Log.d(LOG_TAG, "TTS is closed");
			mTTS.stop();
			mTTS.shutdown();
			mTTS = null;
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
