package edu.cmu.cs.gabriel;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.HashMap;
import java.util.Locale;
import java.util.Timer;
import java.util.TimerTask;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaPlayer;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.MediaController;
import android.widget.VideoView;

import org.json.JSONException;
import org.json.JSONObject;

import edu.cmu.cs.gabriel.network.AccStreamingThread;
import edu.cmu.cs.gabriel.network.AudioStreamingThread;
import edu.cmu.cs.gabriel.network.ControlThread;
import edu.cmu.cs.gabriel.network.LogicalTime;
import edu.cmu.cs.gabriel.network.NetworkProtocol;
import edu.cmu.cs.gabriel.util.PingThread;
import edu.cmu.cs.gabriel.network.ResultReceivingThread;
import edu.cmu.cs.gabriel.network.VideoStreamingThread;
import edu.cmu.cs.gabriel.token.ReceivedPacketInfo;
import edu.cmu.cs.gabriel.token.TokenController;
import edu.cmu.cs.gabriel.util.ResourceMonitoringService;

public class GabrielClientActivity extends Activity implements TextToSpeech.OnInitListener, SensorEventListener{

    private static final String LOG_TAG = "Main";

    // major components for streaming sensor data and receiving information
    private String serverIP = null;
    private VideoStreamingThread videoStreamingThread = null;
    private AccStreamingThread accStreamingThread = null;
    private AudioStreamingThread audioStreamingThread = null;
    private ResultReceivingThread resultThread = null;
    private ControlThread controlThread = null;
    private TokenController tokenController = null;
    private PingThread pingThread = null;

    private boolean isRunning = false;
    private boolean isFirstExperiment = true;

    private CameraPreview preview = null;
    private Camera mCamera = null;
    public byte[] reusedBuffer = null;

    private SensorManager sensorManager = null;
    private Sensor sensorAcc = null;
    private TextToSpeech tts = null;
    private MediaController mediaController = null;

    private ReceivedPacketInfo receivedPacketInfo = null;

    private LogicalTime logicalTime = null;

    private FileWriter controlLogWriter = null;

    // views
    private ImageView imgView = null;
    private VideoView videoView = null;

    // audio
    private AudioRecord audioRecorder = null;
    private Thread audioRecordingThread = null;
    private boolean isAudioRecording = false;
    private int audioBufferSize = -1;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        Log.v(LOG_TAG, "++onCreate");
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED+
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON+
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        imgView = (ImageView) findViewById(R.id.guidance_image);
        videoView = (VideoView) findViewById(R.id.guidance_video);
    }

    @Override
    protected void onResume() {
        Log.v(LOG_TAG, "++onResume");
        super.onResume();

        // dim the screen
//        WindowManager.LayoutParams lp = getWindow().getAttributes();
//        lp.dimAmount = 1.0f;
//        lp.screenBrightness = 0.01f;
//        getWindow().setAttributes(lp);

        initOnce();
        if (Const.IS_EXPERIMENT) { // experiment mode
            runExperiments();
        } else { // demo mode
            serverIP = Const.SERVER_IP;
            initPerRun(serverIP, Const.TOKEN_SIZE, null);
        }
    }

    @Override
    protected void onPause() {
        Log.v(LOG_TAG, "++onPause");
        this.terminate();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        Log.v(LOG_TAG, "++onDestroy");
        super.onDestroy();
    }

    /**
     * Does initialization for the entire application. Called only once even for multiple experiments.
     */
    private void initOnce() {
        Log.v(LOG_TAG, "++initOnce");

        if (Const.SENSOR_VIDEO) {
            preview = (CameraPreview) findViewById(R.id.camera_preview);
            mCamera = preview.checkCamera();
            preview.start();
            mCamera.setPreviewCallbackWithBuffer(previewCallback);
            reusedBuffer = new byte[1920 * 1080 * 3 / 2]; // 1.5 bytes per pixel
            mCamera.addCallbackBuffer(reusedBuffer);
        }

        Const.ROOT_DIR.mkdirs();
        Const.EXP_DIR.mkdirs();

        // TextToSpeech.OnInitListener
        if (tts == null) {
            tts = new TextToSpeech(this, this);
        }

        // Media controller
        if (mediaController == null) {
            mediaController = new MediaController(this);
        }

        // IMU sensors
        if (Const.SENSOR_ACC) {
            if (sensorManager == null) {
                sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
                sensorAcc = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
                sensorManager.registerListener(this, sensorAcc, SensorManager.SENSOR_DELAY_NORMAL);
            }
        }

        // Audio
        if (Const.SENSOR_AUDIO) {
            if (audioRecorder == null) {
                startAudioRecording();
            }
        }

        startResourceMonitoring();

        isRunning = true;
    }

    /**
     * Does initialization before each run (connecting to a specific server).
     * Called once before each experiment.
     */
    private void initPerRun(String serverIP, int tokenSize, File latencyFile) {
        Log.v(LOG_TAG, "++initPerRun");

        if ((pingThread != null) && (pingThread.isAlive())) {
            pingThread.kill();
            pingThread.interrupt();
            pingThread = null;
        }
        if ((videoStreamingThread != null) && (videoStreamingThread.isAlive())) {
            videoStreamingThread.stopStreaming();
            videoStreamingThread = null;
        }
        if ((accStreamingThread != null) && (accStreamingThread.isAlive())) {
            accStreamingThread.stopStreaming();
            accStreamingThread = null;
        }
        if ((audioStreamingThread != null) && (audioStreamingThread.isAlive())) {
            audioStreamingThread.stopStreaming();
            audioStreamingThread = null;
        }
        if ((resultThread != null) && (resultThread.isAlive())) {
            resultThread.close();
            resultThread = null;
        }

        if (Const.IS_EXPERIMENT) {
            if (isFirstExperiment) {
                isFirstExperiment = false;
            } else {
                try {
                    Thread.sleep(20 * 1000);
                } catch (InterruptedException e) {}
                controlThread.sendControlMsg("ping");
                // wait a while for ping to finish...
                try {
                    Thread.sleep(5 * 1000);
                } catch (InterruptedException e) {}
            }
        }
        if (tokenController != null) {
            tokenController.close();
        }
        if ((controlThread != null) && (controlThread.isAlive())) {
            controlThread.close();
            controlThread = null;
        }

        if (serverIP == null) return;

        if (Const.BACKGROUND_PING) {
	        pingThread = new PingThread(serverIP, Const.PING_INTERVAL);
	        pingThread.start();
        }

        logicalTime = new LogicalTime();

        tokenController = new TokenController(tokenSize, latencyFile);

        if (Const.IS_EXPERIMENT) {
            try {
                controlLogWriter = new FileWriter(Const.CONTROL_LOG_FILE);
            } catch (IOException e) {
                Log.e(LOG_TAG, "Control log file cannot be properly opened", e);
            }
        }

        controlThread = new ControlThread(serverIP, Const.CONTROL_PORT, returnMsgHandler, tokenController);
        controlThread.start();

        if (Const.IS_EXPERIMENT) {
            controlThread.sendControlMsg("ping");
            // wait a while for ping to finish...
            try {
                Thread.sleep(5 * 1000);
            } catch (InterruptedException e) {}
        }

        resultThread = new ResultReceivingThread(serverIP, Const.RESULT_RECEIVING_PORT, returnMsgHandler);
        resultThread.start();

        if (Const.SENSOR_VIDEO) {
            videoStreamingThread = new VideoStreamingThread(serverIP, Const.VIDEO_STREAM_PORT, returnMsgHandler, tokenController, mCamera, logicalTime);
            videoStreamingThread.start();
        }

        if (Const.SENSOR_ACC) {
            accStreamingThread = new AccStreamingThread(serverIP, Const.ACC_STREAM_PORT, returnMsgHandler, tokenController);
            accStreamingThread.start();
        }

        if (Const.SENSOR_AUDIO) {
            audioStreamingThread = new AudioStreamingThread(serverIP, Const.AUDIO_STREAM_PORT, returnMsgHandler, tokenController, logicalTime);
            audioStreamingThread.start();
        }
    }

    /**
     * Runs a set of experiments with different server IPs and token numbers.
     * IP list and token sizes are defined in the Const file.
     */
    private void runExperiments() {
        final Timer startTimer = new Timer();
        TimerTask autoStart = new TimerTask() {
            int ipIndex = 0;
            int tokenIndex = 0;
            @Override
            public void run() {
                GabrielClientActivity.this.runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        // end condition
                        if ((ipIndex == Const.SERVER_IP_LIST.length) || (tokenIndex == Const.TOKEN_SIZE_LIST.length)) {
                            Log.d(LOG_TAG, "Finish all experiemets");

                            initPerRun(null, 0, null); // just to get another set of ping results

                            startTimer.cancel();
                            terminate();
                            return;
                        }

                        // make a new configuration
                        serverIP = Const.SERVER_IP_LIST[ipIndex];
                        int tokenSize = Const.TOKEN_SIZE_LIST[tokenIndex];
                        File latencyFile = new File (Const.EXP_DIR.getAbsolutePath() + File.separator +
                                "latency-" + serverIP + "-" + tokenSize + ".txt");
                        Log.i(LOG_TAG, "Start new experiment - IP: " + serverIP +"\tToken: " + tokenSize);

                        // run the experiment
                        initPerRun(serverIP, tokenSize, latencyFile);

                        // move to the next experiment
                        tokenIndex++;
                        if (tokenIndex == Const.TOKEN_SIZE_LIST.length){
                            tokenIndex = 0;
                            ipIndex++;
                        }
                    }
                });
            }
        };

        // run 5 minutes for each experiment
        startTimer.schedule(autoStart, 1000, 5*60*1000);
    }

    private PreviewCallback previewCallback = new PreviewCallback() {
        // called whenever a new frame is captured
        public void onPreviewFrame(byte[] frame, Camera mCamera) {
            if (isRunning) {
                Camera.Parameters parameters = mCamera.getParameters();
                if (videoStreamingThread != null){
                    videoStreamingThread.push(frame, parameters);
                }
            }
            mCamera.addCallbackBuffer(frame);
        }
    };

    /**
     * Notifies token controller that some response is back
     */
    private void notifyToken() {
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_RET_TOKEN;
        receivedPacketInfo.setGuidanceDoneTime(System.currentTimeMillis());
        msg.obj = receivedPacketInfo;
        try {
            tokenController.tokenHandler.sendMessage(msg);
        } catch (NullPointerException e) {
            // might happen because token controller might have been terminated
        }
    }

    private void processServerControl(JSONObject msgJSON) {
        if (Const.IS_EXPERIMENT) {
            try {
                controlLogWriter.write("" + logicalTime.imageTime + "\n");
                String log = msgJSON.toString();
                controlLogWriter.write(log + "\n");
            } catch (IOException e) {}
        }

        try {
            // Switching on/off image sensor
            if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_IMAGE)) {
                boolean sw = msgJSON.getBoolean(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_IMAGE);
                if (sw) { // turning on
                    Const.SENSOR_VIDEO = true;
                    tokenController.reset();
                    if (preview == null) {
                        preview = (CameraPreview) findViewById(R.id.camera_preview);
                        mCamera = preview.checkCamera();
                        preview.start();
                        mCamera.setPreviewCallbackWithBuffer(previewCallback);
                        reusedBuffer = new byte[1920 * 1080 * 3 / 2]; // 1.5 bytes per pixel
                        mCamera.addCallbackBuffer(reusedBuffer);
                    }
                    if (videoStreamingThread == null) {
                        videoStreamingThread = new VideoStreamingThread(serverIP, Const.VIDEO_STREAM_PORT, returnMsgHandler, tokenController, mCamera, logicalTime);
                        videoStreamingThread.start();
                    }
                } else { // turning off
                    Const.SENSOR_VIDEO = false;
                    if (preview != null) {
                        mCamera.setPreviewCallback(null);
                        preview.close();
                        reusedBuffer = null;
                        preview = null;
                        mCamera = null;
                    }
                    if (videoStreamingThread != null) {
                        videoStreamingThread.stopStreaming();
                        videoStreamingThread = null;
                    }
                }
            }

            // Switching on/off ACC sensor
            if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_ACC)) {
                boolean sw = msgJSON.getBoolean(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_ACC);
                if (sw) { // turning on
                    Const.SENSOR_ACC = true;
                    if (sensorManager == null) {
                        sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
                        sensorAcc = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
                        sensorManager.registerListener(this, sensorAcc, SensorManager.SENSOR_DELAY_NORMAL);
                    }
                    if (accStreamingThread == null) {
                        accStreamingThread = new AccStreamingThread(serverIP, Const.ACC_STREAM_PORT, returnMsgHandler, tokenController);
                        accStreamingThread.start();
                    }
                } else { // turning off
                    Const.SENSOR_ACC = false;
                    if (sensorManager != null) {
                        sensorManager.unregisterListener(this);
                        sensorManager = null;
                        sensorAcc = null;
                    }
                    if (accStreamingThread != null) {
                        accStreamingThread.stopStreaming();
                        accStreamingThread = null;
                    }
                }
            }
            // Switching on/off audio sensor
            if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_AUDIO)) {
                boolean sw = msgJSON.getBoolean(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_AUDIO);
                if (sw) { // turning on
                    Const.SENSOR_AUDIO = true;
                    if (audioRecorder == null) {
                        startAudioRecording();
                    }
                    if (audioStreamingThread == null) {
                        audioStreamingThread = new AudioStreamingThread(serverIP, Const.AUDIO_STREAM_PORT, returnMsgHandler, tokenController, logicalTime);
                        audioStreamingThread.start();
                    }
                } else { // turning off
                    Const.SENSOR_AUDIO = false;
                    if (audioRecorder != null) {
                        stopAudioRecording();
                    }
                    if (audioStreamingThread != null) {
                        audioStreamingThread.stopStreaming();
                        audioStreamingThread = null;
                    }
                }
            }

            // Camera configs
            if (preview != null) {
                int targetFps = -1, imgWidth = -1, imgHeight = -1;
                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_FPS))
                    targetFps = msgJSON.getInt(NetworkProtocol.SERVER_CONTROL_FPS);
                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_IMG_WIDTH))
                    imgWidth = msgJSON.getInt(NetworkProtocol.SERVER_CONTROL_IMG_WIDTH);
                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_IMG_HEIGHT))
                    imgHeight = msgJSON.getInt(NetworkProtocol.SERVER_CONTROL_IMG_HEIGHT);
                if (targetFps != -1 || imgWidth != -1)
                    preview.updateCameraConfigurations(targetFps, imgWidth, imgHeight);
            }

        } catch (JSONException e) {
            Log.e(LOG_TAG, "" + msgJSON);
            Log.e(LOG_TAG, "error in processing server control messages" + e);
            return;
        }
    }

    /**
     * Handles messages passed from streaming threads and result receiving threads.
     */
    private Handler returnMsgHandler = new Handler() {
        public void handleMessage(Message msg) {
            if (msg.what == NetworkProtocol.NETWORK_RET_FAILED) {
                //terminate();
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_MESSAGE) {
                receivedPacketInfo = (ReceivedPacketInfo) msg.obj;
                receivedPacketInfo.setMsgRecvTime(System.currentTimeMillis());
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_SPEECH) {
                String ttsMessage = (String) msg.obj;

                if (tts != null){
                    Log.d(LOG_TAG, "tts to be played: " + ttsMessage);
                    // TODO: check if tts is playing something else
                    tts.setSpeechRate(1.0f);
                    String[] splitMSGs = ttsMessage.split("\\.");
                    HashMap<String, String> map = new HashMap<String, String>();
                    map.put(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "unique");

                    if (splitMSGs.length == 1)
                        tts.speak(splitMSGs[0].toString().trim(), TextToSpeech.QUEUE_FLUSH, map); // the only sentence
                    else {
                        tts.speak(splitMSGs[0].toString().trim(), TextToSpeech.QUEUE_FLUSH, null); // the first sentence
                        for (int i = 1; i < splitMSGs.length - 1; i++) {
                            tts.playSilence(350, TextToSpeech.QUEUE_ADD, null); // add pause for every period
                            tts.speak(splitMSGs[i].toString().trim(),TextToSpeech.QUEUE_ADD, null);
                        }
                        tts.playSilence(350, TextToSpeech.QUEUE_ADD, null);
                        tts.speak(splitMSGs[splitMSGs.length - 1].toString().trim(),TextToSpeech.QUEUE_ADD, map); // the last sentence
                    }
                }
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_IMAGE || msg.what == NetworkProtocol.NETWORK_RET_ANIMATION) {
                Bitmap feedbackImg = (Bitmap) msg.obj;
                imgView = (ImageView) findViewById(R.id.guidance_image);
                videoView = (VideoView) findViewById(R.id.guidance_video);
                imgView.setVisibility(View.VISIBLE);
                videoView.setVisibility(View.GONE);
                imgView.setImageBitmap(feedbackImg);
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_VIDEO) {
                String url = (String) msg.obj;
                imgView.setVisibility(View.GONE);
                videoView.setVisibility(View.VISIBLE);
                videoView.setVideoURI(Uri.parse(url));
                videoView.setMediaController(mediaController);
                //Video Loop
                videoView.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                    public void onCompletion(MediaPlayer mp) {
                        videoView.start();
                    }
                });
                videoView.start();
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_DONE) {
                notifyToken();
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_CONFIG) {
                String controlMsg = (String) msg.obj;
                try {
                    final JSONObject controlJSON = new JSONObject(controlMsg);
                    if (controlJSON.has("delay")) {
                        final long delay = controlJSON.getInt("delay");

                        final Timer controlTimer = new Timer();
                        TimerTask controlTask = new TimerTask() {
                            @Override
                            public void run() {
                                GabrielClientActivity.this.runOnUiThread(new Runnable() {
                                    @Override
                                    public void run() {
                                        logicalTime.increaseImageTime((int) (delay * 15 / 1000));
                                        processServerControl(controlJSON);
                                    }
                                });
                            }
                        };

                        // run 5 minutes for each experiment
                        controlTimer.schedule(controlTask, delay);
                    } else {
                        processServerControl(controlJSON);
                    }
                } catch (JSONException e) {
                    Log.e(LOG_TAG, "error in jsonizing server control messages" + e);
                }
            }
        }
    };

    /**
     * Terminates all services.
     */
    private void terminate() {
        Log.v(LOG_TAG, "++terminate");

        isRunning = false;

        if ((pingThread != null) && (pingThread.isAlive())) {
            pingThread.kill();
            pingThread.interrupt();
            pingThread = null;
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
        if ((audioStreamingThread != null) && (audioStreamingThread.isAlive())) {
            audioStreamingThread.stopStreaming();
            audioStreamingThread = null;
        }
        if ((controlThread != null) && (controlThread.isAlive())) {
            controlThread.close();
            controlThread = null;
        }
        if (tokenController != null){
            tokenController.close();
            tokenController = null;
        }
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        if (preview != null) {
            mCamera.setPreviewCallback(null);
            preview.close();
            reusedBuffer = null;
            preview = null;
            mCamera = null;
        }
        if (sensorManager != null) {
            sensorManager.unregisterListener(this);
            sensorManager = null;
            sensorAcc = null;
        }
        if (audioRecorder != null) {
            stopAudioRecording();
        }
        stopResourceMonitoring();
        if (Const.IS_EXPERIMENT) {
            try {
                controlLogWriter.close();
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in closing control log file");
            }
        }
    }

    /**************** SensorEventListener ***********************/
    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        /*
         * Currently only ACC sensor is supported
         */
        if (event.sensor.getType() != Sensor.TYPE_ACCELEROMETER)
            return;
        if (accStreamingThread != null) {
            accStreamingThread.push(event.values);
        }
    }
    /**************** End of SensorEventListener ****************/

    /**************** TextToSpeech.OnInitListener ***************/
    public void onInit(int status) {
        if (status == TextToSpeech.SUCCESS) {
            if (tts == null) {
                tts = new TextToSpeech(this, this);
            }
            int result = tts.setLanguage(Locale.US);
            if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                Log.e(LOG_TAG, "Language is not available.");
            }
            int listenerResult = tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override
                public void onDone(String utteranceId) {
                    Log.v(LOG_TAG,"progress on Done " + utteranceId);
//                  notifyToken();
                }
                @Override
                public void onError(String utteranceId) {
                    Log.v(LOG_TAG,"progress on Error " + utteranceId);
                }
                @Override
                public void onStart(String utteranceId) {
                    Log.v(LOG_TAG,"progress on Start " + utteranceId);
                }
            });
            if (listenerResult != TextToSpeech.SUCCESS) {
                Log.e(LOG_TAG, "failed to add utterance progress listener");
            }
        } else {
            // Initialization failed.
            Log.e(LOG_TAG, "Could not initialize TextToSpeech.");
        }
    }
    /**************** End of TextToSpeech.OnInitListener ********/

    /**************** Audio recording ***************************/
    private void startAudioRecording() {
        audioBufferSize = AudioRecord.getMinBufferSize(Const.RECORDER_SAMPLERATE, Const.RECORDER_CHANNELS, Const.RECORDER_AUDIO_ENCODING);
        Log.d(LOG_TAG, "buffer size of audio recording: " + audioBufferSize);
        audioRecorder = new AudioRecord(MediaRecorder.AudioSource.MIC,
                Const.RECORDER_SAMPLERATE, Const.RECORDER_CHANNELS,
                Const.RECORDER_AUDIO_ENCODING, audioBufferSize);
        audioRecorder.startRecording();

        isAudioRecording = true;

        audioRecordingThread = new Thread(new Runnable() {
            @Override
            public void run() {
                readAudioData();
            }
        }, "AudioRecorder Thread");
        audioRecordingThread.start();
    }

    private void readAudioData() {
        byte data[] = new byte[audioBufferSize];

        while (isAudioRecording) {
            int n = audioRecorder.read(data, 0, audioBufferSize);

            if (n != AudioRecord.ERROR_INVALID_OPERATION && n > 0) {
                if (audioStreamingThread != null) {
                    audioStreamingThread.push(data);
                }
            }
        }
    }

    private void stopAudioRecording() {
        isAudioRecording = false;
        if (audioRecorder != null) {
            if (audioRecorder.getState() == AudioRecord.STATE_INITIALIZED)
                audioRecorder.stop();
            audioRecorder.release();
            audioRecorder = null;
            audioRecordingThread = null;
        }
    }
    /**************** End of audio recording ********************/

    /**************** Battery recording *************************/
    /*
	 * Resource monitoring of the mobile device
     * Checks battery and CPU usage, as well as device temperature
	 */
    Intent resourceMonitoringIntent = null;

    public void startResourceMonitoring() {
        Log.i(LOG_TAG, "Starting Battery Recording Service");
        resourceMonitoringIntent = new Intent(this, ResourceMonitoringService.class);
        startService(resourceMonitoringIntent);
    }

    public void stopResourceMonitoring() {
        Log.i(LOG_TAG, "Stopping Battery Recording Service");
        if (resourceMonitoringIntent != null) {
            stopService(resourceMonitoringIntent);
            resourceMonitoringIntent = null;
        }
    }
    /**************** End of battery recording ******************/
}
