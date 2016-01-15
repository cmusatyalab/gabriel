package edu.cmu.cs.gabriel;

import java.io.File;
import java.util.HashMap;
import java.util.Locale;
import java.util.Timer;
import java.util.TimerTask;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.preference.PreferenceManager;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import edu.cmu.cs.gabriel.network.AccStreamingThread;
import edu.cmu.cs.gabriel.network.NetworkProtocol;
import edu.cmu.cs.gabriel.network.ResultReceivingThread;
import edu.cmu.cs.gabriel.network.VideoStreamingThread;
import edu.cmu.cs.gabriel.token.ReceivedPacketInfo;
import edu.cmu.cs.gabriel.token.TokenController;

public class GabrielClientActivity extends Activity implements TextToSpeech.OnInitListener, SensorEventListener{

    private static final String LOG_TAG = "Main";

    private static final int SETTINGS_ID = Menu.FIRST;
    private static final int CHANGE_SETTING_CODE = 2;

    public static final int VIDEO_STREAM_PORT = 9098;
    public static final int ACC_STREAM_PORT = 9099;
    public static final int GPS_PORT = 9100;
    public static final int RESULT_RECEIVING_PORT = 9101;


    VideoStreamingThread videoStreamingThread;
    AccStreamingThread accStreamingThread;
    ResultReceivingThread resultThread;
    TokenController tokenController = null;

    private SharedPreferences sharedPref;
    private boolean hasStarted;
    private CameraPreview mPreview;

    private SensorManager mSensorManager = null;
    private Sensor mAccelerometer = null;
    protected TextToSpeech mTTS = null;

    private ReceivedPacketInfo receivedPacketInfo;

    private int fpsCounter = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        Log.d(LOG_TAG, "++onCreate");
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED+
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON+
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        // Connect to Gabriel Server if it's not experiment
        if (!Const.IS_EXPERIMENT){
            final Button expButton = (Button) findViewById(R.id.button_runexperiment);
            expButton.setVisibility(View.GONE);
            init_once();
            init_experiement();
        }
    }

    private void init_once() {
        Log.d(LOG_TAG, "on init once");
        mPreview = (CameraPreview) findViewById(R.id.camera_preview);
        mPreview.setPreviewCallback(previewCallback);
        Const.ROOT_DIR.mkdirs();
        Const.LATENCY_DIR.mkdirs();
        // TextToSpeech.OnInitListener
        if (mTTS == null) {
            mTTS = new TextToSpeech(this, this);
        }
        if (mSensorManager == null) {
            mSensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
            mAccelerometer = mSensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            mSensorManager.registerListener(this, mAccelerometer, SensorManager.SENSOR_DELAY_NORMAL);
        }
        hasStarted = true;
    }
    private void init_experiement() {
        Log.e(LOG_TAG, "on init experiment - cleaning up");
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

        if (Const.IS_EXPERIMENT) {
            try {
                Thread.sleep(20*1000);
            } catch (InterruptedException e) {}
        }

        Log.e(LOG_TAG, "on init experiment - starting up");
        tokenController = new TokenController(Const.LATENCY_FILE);
        resultThread = new ResultReceivingThread(Const.GABRIEL_IP, RESULT_RECEIVING_PORT, returnMsgHandler, tokenController);
        resultThread.start();

        videoStreamingThread = new VideoStreamingThread(Const.GABRIEL_IP, VIDEO_STREAM_PORT, returnMsgHandler, tokenController);
        videoStreamingThread.start();

        accStreamingThread = new AccStreamingThread(Const.GABRIEL_IP, ACC_STREAM_PORT, returnMsgHandler, tokenController);
        accStreamingThread.start();
    }

    boolean experimentStarted = false;
    public void startExperiment(View view) {
        if (!experimentStarted) {
            // Automate experiment
            experimentStarted = true;
            runExperiements();
        }
    }

    protected void runExperiements(){
        final Timer startTimer = new Timer();
        TimerTask autoStart = new TimerTask(){
            String[] ipList = {
                    "128.2.213.106",
                    "54.198.72.157",
                    "54.190.77.230",
                    "54.155.98.138",

                    // GPU on Amazon EC2
                //  "54.146.224.125",
                //  "54.218.199.49",
                //  "54.155.64.83",
                    };

            int[] tokenSize = {1};
//          int[] tokenSize = {10000};
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
                            terminate();
                            return;
                        }

                        // make a new configuration
                        Const.GABRIEL_IP = ipList[ipIndex];
                        Const.MAX_TOKEN_SIZE = tokenSize[tokenIndex];
                        Const.LATENCY_FILE_NAME = "latency-" + Const.GABRIEL_IP + "-" + Const.MAX_TOKEN_SIZE + ".txt";
                        Const.LATENCY_FILE = new File (Const.ROOT_DIR.getAbsolutePath() +
                                File.separator + "exp" +
                                File.separator + Const.LATENCY_FILE_NAME);
                        Log.e(LOG_TAG, "Start new experiment");
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

        // run 5 minutes for each experiment
        init_once();
        startTimer.schedule(autoStart, 1000, 5*60*1000);
    }

    // Implements TextToSpeech.OnInitListener
    public void onInit(int status) {
        if (status == TextToSpeech.SUCCESS) {
            if (mTTS == null){
                mTTS = new TextToSpeech(this, this);
            }
            int result = mTTS.setLanguage(Locale.US);
            if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                Log.e(LOG_TAG, "Language is not available.");
            }
            int listenerResult = mTTS.setOnUtteranceProgressListener(new UtteranceProgressListener()
            {
                @Override
                public void onDone(String utteranceId)
                {
                    Log.i(LOG_TAG,"progress on Done " + utteranceId);
//                  notifyToken();
                }

                @Override
                public void onError(String utteranceId)
                {
                    Log.d(LOG_TAG,"progress on Error " + utteranceId);
                }

                @Override
                public void onStart(String utteranceId)
                {
                    Log.d(LOG_TAG,"progress on Start " + utteranceId);
                }
            });
            if (listenerResult != TextToSpeech.SUCCESS)
            {
                Log.e(LOG_TAG, "failed to add utterance progress listener");
            }
        } else {
            // Initialization failed.
            Log.e(LOG_TAG, "Could not initialize TextToSpeech.");
        }
    }

    private void notifyToken() {
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_RET_TOKEN;
        receivedPacketInfo.setGuidanceDoneTime(System.currentTimeMillis());
        msg.obj = receivedPacketInfo;
        tokenController.tokenHandler.sendMessage(msg);
    }

    @Override
    protected void onResume() {
        super.onResume();
        Log.d(LOG_TAG, "++onResume");
    }

    @Override
    protected void onPause() {
        super.onPause();
        Log.d(LOG_TAG, "++onPause");
        this.terminate();
        Log.d(LOG_TAG, "--onPause");
    }

    @Override
    protected void onDestroy() {
        this.terminate();
        super.onDestroy();
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        Intent intent;

        switch (item.getItemId()) {
        case SETTINGS_ID:
            intent = new Intent().setClass(this, SettingsActivity.class);
            startActivityForResult(intent, CHANGE_SETTING_CODE);
            break;
        }

        return super.onOptionsItemSelected(item);
    }


    private PreviewCallback previewCallback = new PreviewCallback() {
        public void onPreviewFrame(byte[] frame, Camera mCamera) {

            if (hasStarted) {
                Camera.Parameters parameters = mCamera.getParameters();
                if (videoStreamingThread != null){
                    if (fpsCounter == 0) {
                        //Log.d(LOG_TAG, "begin: "+System.currentTimeMillis());
                        videoStreamingThread.push(frame, parameters);
                        //Log.d(LOG_TAG, "end: "+System.currentTimeMillis());
                    }
//                  fpsCounter = (fpsCounter + 1) % 2;
                }
            }
        }
    };

    private Handler returnMsgHandler = new Handler() {
        public void handleMessage(Message msg) {
            if (msg.what == NetworkProtocol.NETWORK_RET_FAILED) {
                Bundle data = msg.getData();
                String message = data.getString("message");
//              stopStreaming();
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_MESSAGE) {
                receivedPacketInfo = (ReceivedPacketInfo) msg.obj;
                receivedPacketInfo.setMsgRecvTime(System.currentTimeMillis());
                Log.i(LOG_TAG, "ddd:" + System.currentTimeMillis());
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_SPEECH) {
                String ttsMessage = (String) msg.obj;

                if (mTTS != null){
                    Log.i(LOG_TAG, "tts string: " + ttsMessage);
                    if (Const.PLAY_PRERECORDED) {
                        if (ttsMessage.equals("left")) {
                            //Play pre-recorded audio
                            Log.i(LOG_TAG, "Playing pre-recorded audio " + ttsMessage + ".wav");
                            return;
                        }
                        if (ttsMessage.equals("right")) {
                            //Play pre-recorded audio
                            Log.i(LOG_TAG, "Playing pre-recorded audio " + ttsMessage + ".wav");
                            return;
                        }
                    }

                    mTTS.setSpeechRate(1f);
                    String[] splitMSGs = ttsMessage.split("\\.");
                    HashMap<String, String> map = new HashMap<String, String>();
                    map.put(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "unique");

                    if (splitMSGs.length == 1)
                        mTTS.speak(splitMSGs[0].toString().trim(), TextToSpeech.QUEUE_FLUSH, map); // the only sentence
                    else {
                        mTTS.speak(splitMSGs[0].toString().trim(), TextToSpeech.QUEUE_FLUSH, null); // the first sentence
                        for (int i = 1; i < splitMSGs.length - 1; i++) {
                            mTTS.playSilence(350, TextToSpeech.QUEUE_ADD, null); // add pause for every period
                            mTTS.speak(splitMSGs[i].toString().trim(),TextToSpeech.QUEUE_ADD, null);
                        }
                        mTTS.playSilence(350, TextToSpeech.QUEUE_ADD, null);
                        mTTS.speak(splitMSGs[splitMSGs.length - 1].toString().trim(),TextToSpeech.QUEUE_ADD, map); // the last sentence
                    }
                }
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_IMAGE) {
                Bitmap feedbackImg = (Bitmap) msg.obj;
                ImageView img = (ImageView) findViewById(R.id.guidance_image);
                img.setImageBitmap(feedbackImg);
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_DONE) {
                notifyToken();
            }
        }
    };

    public void setDefaultPreferences() {
        // setDefaultValues will only be invoked if it has not been invoked
        PreferenceManager.setDefaultValues(this, R.xml.preferences, false);
        sharedPref = PreferenceManager.getDefaultSharedPreferences(this);

        sharedPref.edit().putBoolean(SettingsActivity.KEY_PROXY_ENABLED, true);
        sharedPref.edit().putString(SettingsActivity.KEY_PROTOCOL_LIST, "UDP");
        sharedPref.edit().putString(SettingsActivity.KEY_PROXY_IP, "128.2.207.54");
        sharedPref.edit().putInt(SettingsActivity.KEY_PROXY_PORT, 8080);
        sharedPref.edit().commit();
    }

    public void getPreferences() {
        sharedPref = PreferenceManager.getDefaultSharedPreferences(this);
        String sProtocol = sharedPref.getString(SettingsActivity.KEY_PROTOCOL_LIST, "UDP");
        String[] sProtocolList = getResources().getStringArray(R.array.protocol_list);
    }

    private void terminate() {
        Log.d(LOG_TAG, "on terminate");
        // change only soft state

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
            mTTS.stop();
            mTTS.shutdown();
            mTTS = null;
            Log.d(LOG_TAG, "TTS is closed");
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
//          accStreamingThread.push(event.values);
        }
        // Log.d(LOG_TAG, "acc_x : " + mSensorX + "\tacc_y : " + mSensorY);
    }
}
