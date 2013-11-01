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

	public int PROTOCOL_INDEX = VideoStreamingThread.PROTOCOL_TCP;
	public String REMOTE_IP = "128.2.210.197";
	public static int REMOTE_CONTROL_PORT = 5000;
	public static int REMOTE_DATA_PORT = 9098;
	public static int FPS_LIMITATION = 100;

	CameraConnector cameraRecorder;
	VideoStreamingThread streamingThread;
	ControlThread controlThread;

	private SharedPreferences sharedPref;
	private boolean hasStarted;
	private TextView statView;
	private CameraPreview mPreview;
	private BufferedOutputStream localOutputStream;

	protected TextToSpeech mTTS;

	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        init();
	}

	private void init() {
		mPreview = (CameraPreview) findViewById(R.id.camera_preview);
		
		// TextToSpeech.OnInitListener
		mTTS = new TextToSpeech(this, this);
		cameraRecorder = null;
		streamingThread = null;
		controlThread = null;
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
		private long frameCount = 0, firstUpdateTime = 0;
		private long prevUpdateTime = 0, currentUpdateTime = 0;
		private long expected_time_delay = 1000 / FPS_LIMITATION;

		public void onPreviewFrame(byte[] frame, Camera mCamera) {
			if (hasStarted && (localOutputStream != null)) {
				Camera.Parameters parameters = mCamera.getParameters();
				Camera.Size size = parameters.getPreviewSize();
				
				if (firstUpdateTime == 0) {
					firstUpdateTime = System.currentTimeMillis();
				}
				currentUpdateTime = System.currentTimeMillis();
				frameCount++;
				int interval = 10;
				if (frameCount % interval == 0) {
//					Log.d(LOG_TAG, "Current FPS: " + 1000.0 * 1 / (currentUpdateTime - prevUpdateTime));
//					Log.d(LOG_TAG, "Image size : " + size.width + "x" + size.height);
				}
				prevUpdateTime = currentUpdateTime;

				
				YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), size.width, size.height, null);
				ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
				image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 95, tmpBuffer);
				streamingThread.push(tmpBuffer.toByteArray());
			}
		}
	};

	private Handler returnMsgHandler = new Handler() {
		public void handleMessage(Message msg) {
			if (msg.what == VideoStreamingThread.NETWORK_RET_FAILED) {
				Bundle data = msg.getData();
				String message = data.getString("message");
				stopStreaming();
				new AlertDialog.Builder(GabrielClientActivity.this).setTitle("INFO").setMessage(message)
						.setIcon(R.drawable.ic_launcher).setNegativeButton("Confirm", null).show();
			} else if (msg.what == VideoStreamingThread.NETWORK_RET_RESULT) {
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
				Log.i(LOG_TAG, "connected to control channel " + REMOTE_IP + ":" + REMOTE_CONTROL_PORT);
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

				if (streamingThread == null) {
					streamingThread = new VideoStreamingThread(PROTOCOL_INDEX,
							cameraRecorder.getOutputFileDescriptor(), REMOTE_IP, serverStreamPort, returnMsgHandler);
					streamingThread.start();
				}

				Log.i(LOG_TAG, "StreamingThread starts");

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
	
	private void startCapture(){
		if (hasStarted == false) {
			mPreview.setPreviewCallback(previewCallback);
			Log.d(LOG_TAG, "connecting to control channel " + REMOTE_IP + ":" + REMOTE_CONTROL_PORT);
			controlThread = new ControlThread(controlMsgHandler, REMOTE_IP, REMOTE_CONTROL_PORT);
			controlThread.start();
		}
	}
	
	public void startStreaming(View view) throws IOException {
		startCapture();
	}

	public void stopStreaming() {
		hasStarted = false;
		mPreview.setPreviewCallback(null);
		if (streamingThread != null && streamingThread.isAlive()) {
			streamingThread.stopStreaming();
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

		if (sProtocol.compareToIgnoreCase(sProtocolList[0]) == 0)
			setProtocolIndex(VideoStreamingThread.PROTOCOL_UDP);
		else if (sProtocol.compareToIgnoreCase(sProtocolList[1]) == 0)
			setProtocolIndex(VideoStreamingThread.PROTOCOL_TCP);
		else if (sProtocol.compareToIgnoreCase(sProtocolList[2]) == 0)
			setProtocolIndex(VideoStreamingThread.PROTOCOL_RTPUDP);
		else
			setProtocolIndex(VideoStreamingThread.PROTOCOL_RTPTCP);

		REMOTE_IP = sharedPref.getString(SettingsActivity.KEY_PROXY_IP, "128.2.213.25");
		REMOTE_CONTROL_PORT = Integer.parseInt(sharedPref.getString(SettingsActivity.KEY_PROXY_PORT, "8080"));

		Log.v("pref", "remotePort is" + REMOTE_CONTROL_PORT);

	}

	public int getProtocolIndex() {
		return PROTOCOL_INDEX;
	}

	public void setProtocolIndex(int protocolIndex) {
		this.PROTOCOL_INDEX = protocolIndex;
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
		if (controlThread != null) {
			Message msg_out = Message.obtain();
			msg_out.what = ControlThread.CODE_CLOSE_CONNECTION;
			Handler controlHandler = controlThread.getHandler();
			controlHandler.sendMessage(msg_out);
		}
		if (cameraRecorder != null) {
			cameraRecorder.close();
		}
		if ((streamingThread != null) && (streamingThread.isAlive())) {
			streamingThread.stopStreaming();
		}
		if (mPreview != null) {
			mPreview.setPreviewCallback(null);
			mPreview.close();
		}
		// Don't forget to shutdown!
		if (mTTS != null) {
			mTTS.stop();
			mTTS.shutdown();
		}
		finish();
	}	
}
