package edu.cmu.cs.elijah.application.OR;

import java.io.File;
import java.util.ArrayList;
import java.util.Locale;

import edu.cmu.cs.elijah.application.or.R;

import android.hardware.Camera;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.Intent;
import android.speech.tts.TextToSpeech;
import android.util.Log;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.View;
import android.view.Window;
import android.widget.Button;
import android.widget.ScrollView;
import android.widget.TextView;

public class ObjectRecognitionActivity extends Activity implements TextToSpeech.OnInitListener {
	
//	private static final String CLOUDLET_IP_ADDRESS = "cloudlet.krha.kr";
//	private static final String CLOUDLET_IP_ADDRESS = "eclcs42.ugrad.cs.cmu.edu";
	private static final String CLOUDLET_IP_ADDRESS = "typhoon.elijah.cs.cmu.edu";
	private static final int CLOUDLET_PORT = 9099;

	public static final String TAG = "krha_app";
	public static final String TEST_IMAGE_PATH = "/mnt/sdcard/Cloudlet/MOPED/";
	protected ArrayList<File> testImageList;

	protected static String server_ipaddress;
	protected static int server_port;
	protected ObjectRecognitionNetworkClient client;

	protected ProgressDialog mDialog;
	protected boolean gFocussed = false;
	protected boolean gCameraPressed = false;
	protected Uri mImageCaptureUri;
	protected Button mSendButton;
	protected Button mTestButton;

	protected byte[] mImageData;
	protected Preview mPreview;
	protected TextToSpeech mTTS;

	// time stamp for test
	protected long startApp;
	protected long endApp;
	private TextView textView;
	private ScrollView scrollView;

	@Override
	public void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		requestWindowFeature(Window.FEATURE_NO_TITLE);
		setContentView(R.layout.activity_object_recognition);
		mPreview = (Preview) findViewById(R.id.camera_preview);

		Bundle extras = getIntent().getExtras();
		if (extras != null) {
			server_ipaddress = extras.getString("address");
			server_port = extras.getInt("port", ObjectRecognitionActivity.CLOUDLET_PORT);
		} else {
			server_ipaddress = ObjectRecognitionActivity.CLOUDLET_IP_ADDRESS;
			server_port = ObjectRecognitionActivity.CLOUDLET_PORT;
		}

		// buttons
		mSendButton = (Button) findViewById(R.id.sendButton);
		mSendButton.setOnClickListener(new View.OnClickListener() {
			@Override
			public void onClick(View v) {
				// capture image
				if (mPreview.mCamera != null) {
					mPreview.mCamera.takePicture(null, null, mPictureCallbackJpeg);
				}
			}
		});

		// TextToSpeech.OnInitListener
		mTTS = new TextToSpeech(this, this);

	}

	@Override
	public boolean onCreateOptionsMenu(Menu menu) {
		// Inflate the menu; this adds items to the action bar if it is present.
		getMenuInflater().inflate(R.menu.activity_object_recognition, menu);
		return true;
	}

	/*
	 * Network Event Handler
	 */
	Handler networkHandler = new Handler() {
		public void handleMessage(Message msg) {			
			if (msg.what == ObjectRecognitionNetworkClient.FEEDBACK) {
				Bundle data = msg.getData();
				String message = data.getString("message");
				updateLog(message + "\n");

				// Run TTS
				// TTSFeedback(message);
			} else if (msg.what == ObjectRecognitionNetworkClient.ERROR) {
				Bundle data = msg.getData();
				String message = data.getString("message");
				new AlertDialog.Builder(ObjectRecognitionActivity.this).setTitle("Info").setMessage(message)
						.setIcon(R.drawable.ic_launcher).setNegativeButton("Confirm", null).show();
			}

			// Dissmiss Dialog
			if (mDialog != null && mDialog.isShowing()){
				mDialog.dismiss();
				mDialog = null;
			}
		}
	};

	/*
	 * Upload Log
	 */
	public void updateLog(String msg) {
		if (this.textView != null) {
			this.textView.append(msg);
			this.scrollView.post(new Runnable() {
				public void run() {
					scrollView.fullScroll(View.FOCUS_DOWN);
				}
			});
		}
	}

	/*
	 * TTS
	 */
	private static final String FEEDBACK_PREFIX = "Found items are ";

	private void TTSFeedback(String ttsString) {
		// Show Application Runtime
		/*
		 * String message = "Time for app run\n start: " + startApp + "\nend: "
		 * + endApp + "\ndiff: " + (endApp-startApp); new
		 * AlertDialog.Builder(CloudletCameraActivity.this).setTitle("Info")
		 * .setMessage(message) .setIcon(R.drawable.ic_launcher)
		 * .setNegativeButton("Confirm", null) .show();
		 */

		// Select a random hello.
		Log.d("krha", "tts string origin: " + ttsString);
		String[] objects = ttsString.split(" ");
		if (ttsString == null || objects == null || objects.length == 0 || ttsString.trim().length() == 0) {
			mTTS.speak("Found nothing", TextToSpeech.QUEUE_FLUSH, null);
		} else if (objects.length == 1) {
			StringBuffer sb = new StringBuffer();
			sb.append(objects[0].replace("_", " "));
			mTTS.speak(sb.toString(), TextToSpeech.QUEUE_FLUSH, null);
		} else {
			StringBuffer sb = new StringBuffer();
			for (int i = 0; i < objects.length; i++) {
				sb.append(objects[i].replaceAll("_", " "));
				if (i != objects.length - 1) {
					sb.append(" and ");
				}
			}
			Log.d("krha", "tts string : " + sb.toString());
			mTTS.setSpeechRate(1f);
			mTTS.speak(sb.toString(), TextToSpeech.QUEUE_FLUSH, null);
		}

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

	/*
	 * Camera Capture
	 */
	Camera.PictureCallback mPictureCallbackJpeg = new Camera.PictureCallback() {
		public void onPictureTaken(byte[] data, Camera c) {
			// time stamp
			mImageData = data;
			saveImage(mImageData);

			if (mPreview.mCamera != null) {
				try {
					mPreview.mCamera.startPreview();
				} catch (Exception e) {

				}
			}
		}
	};

	public void saveImage(byte[] data) {

		// upload image
		if (client == null) {
			client = new ObjectRecognitionNetworkClient(this, ObjectRecognitionActivity.this, networkHandler);
			client.initConnection(server_ipaddress, server_port);
			client.start();
		}

		// check network connection
		mDialog = ProgressDialog.show(ObjectRecognitionActivity.this, "", "Uploading..\n", true, true);
		client.uploadImage(data);
	}

	Camera.AutoFocusCallback cb = new Camera.AutoFocusCallback() {
		public void onAutoFocus(boolean success, Camera c) {

			if (success) {
				ToneGenerator tg = new ToneGenerator(AudioManager.STREAM_SYSTEM, 100);
				if (tg != null)
					tg.startTone(ToneGenerator.TONE_PROP_BEEP2);
				gFocussed = true;
				try {
					if (gCameraPressed) {
						if (mPreview.mCamera != null) {
							mPreview.mCamera.takePicture(null, null, mPictureCallbackJpeg);
						}
					}
				} catch (Exception e) {
					Log.i("Exc", e.toString());
				}
			} else {
				ToneGenerator tg = new ToneGenerator(AudioManager.STREAM_SYSTEM, 100);
				if (tg != null)
					tg.startTone(ToneGenerator.TONE_PROP_BEEP2);

				try {
					if (gCameraPressed) {
						if (mPreview.mCamera != null) {
							mPreview.mCamera.takePicture(null, null, mPictureCallbackJpeg);
						}
					}
				} catch (Exception e) {
					Log.i("Exc", e.toString());
				}
			}
		}
	};

	/*
	 * Destroy
	 * 
	 * @see android.app.Activity#onKeyDown(int, android.view.KeyEvent)
	 */
	public boolean onKeyDown(int keyCode, KeyEvent event) {
		if (keyCode == KeyEvent.KEYCODE_BACK) {

			Intent caller = getIntent();
			caller.putExtra("message", "finish");
			setResult(RESULT_OK, caller);
			finish();

			// Intent intent = new Intent(CloudletCameraActivity.this,
			// CloudletActivity.class);
			// startActivity(intent);
			// finish();
		}
		return super.onKeyDown(keyCode, event);
	}

	@Override
	public void onDestroy() {
		if (client != null)
			client.close();

		if (mPreview != null)
			mPreview.close();
		// Don't forget to shutdown!
		if (mTTS != null) {
			mTTS.stop();
			mTTS.shutdown();
		}
		super.onDestroy();
	}

}
