package edu.cmu.cs.gabrielclient;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.HashMap;
import java.util.Locale;
import java.util.Timer;
import java.util.TimerTask;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Bitmap;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.media.AudioRecord;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.media.MediaRecorder;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Message;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.MediaController;
import android.widget.VideoView;
import android.widget.TextView;

import edu.cmu.cs.gabrielclient.network.EngineInput;
import edu.cmu.cs.gabrielclient.network.FrameSupplier;
import edu.cmu.cs.gabrielclient.network.InstructionComm;
import edu.cmu.cs.gabrielclient.network.NetworkProtocol;
import edu.cmu.cs.gabrielclient.util.ResourceMonitoringService;

public class GabrielClientActivity extends Activity implements TextToSpeech.OnInitListener, SensorEventListener{

    private static final String LOG_TAG = "Main";

    // major components for streaming sensor data and receiving information
    private String serverIP = null;
    InstructionComm comm;
    private EngineInput engineInput;
    final private Object engineInputLock = new Object();
    private FrameSupplier frameSupplier;

    private boolean isRunning = false;
    private boolean isFirstExperiment = true;

    private CameraPreview preview = null;

    private SensorManager sensorManager = null;
    private Sensor sensorAcc = null;
    private TextToSpeech tts = null;
    private MediaController mediaController = null;

    private FileWriter controlLogWriter = null;

    // views
    private ImageView imgView = null;
    private VideoView videoView = null;
    private TextView subtitleView = null;

    // audio
    private AudioRecord audioRecorder = null;
    private Thread audioRecordingThread = null;
    private boolean isAudioRecording = false;
    private int audioBufferSize = -1;

    // Background threads based on
    // https://github.com/googlesamples/android-Camera2Basic/blob/master/Application/src/main/java/com/example/android/camera2basic/Camera2BasicFragment.java#L652
    /**
     * Thread for running tasks that shouldn't block the UI.
     */
    private HandlerThread backgroundThread;

    /**
     * A {@link Handler} for running tasks in the background.
     */
    private Handler backgroundHandler;

    /**
     * Starts a background thread and its {@link Handler}.
     */
    private void startBackgroundThread() {
        backgroundThread = new HandlerThread("ImageUpload");
        backgroundThread.start();
        backgroundHandler = new Handler(backgroundThread.getLooper());

        backgroundHandler.post(imageUpload);
    }

    /**
     * Stops the background thread and its {@link Handler}.
     */
    private void stopBackgroundThread() {
        backgroundThread.quitSafely();
        try {
            backgroundThread.join();
            backgroundThread = null;
            backgroundHandler = null;
        } catch (InterruptedException e) {
            e.printStackTrace();
        }
    }

    public EngineInput getEngineInput() {
        EngineInput engineInput;
        synchronized (this.engineInputLock) {
            try {
                while (isRunning && this.engineInput == null) {
                    engineInputLock.wait();
                }

                engineInput = this.engineInput;
                this.engineInput = null;  // Prevent sending the same frame again
            } catch (/* InterruptedException */ Exception e) {
                Log.e(LOG_TAG, "Error waiting for engine input", e);
                engineInput = null;
            }
        }
        return engineInput;
    }

    private Runnable imageUpload = new Runnable() {
        @Override
        public void run() {
            comm.sendSupplier(GabrielClientActivity.this.frameSupplier);

            if (isRunning) {
                backgroundHandler.post(imageUpload);
            }
        }
    };

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
        if(Const.SHOW_SUBTITLES){
            findViewById(R.id.subtitleText).setVisibility(View.VISIBLE);
        }
        if (Const.SAVE_FRAME_SEQUENCE){
            Const.SAVE_FRAME_SEQUENCE_DIR.mkdirs();
        }
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
            preview.start(CameraPreview.CameraConfiguration.getInstance(), previewCallback);
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

        if (serverIP == null) return;

        if (Const.IS_EXPERIMENT) {
            try {
                controlLogWriter = new FileWriter(Const.CONTROL_LOG_FILE);
            } catch (IOException e) {
                Log.e(LOG_TAG, "Control log file cannot be properly opened", e);
            }
        }

        this.setupComm();
        this.startBackgroundThread();
    }

    void setupComm() {
        this.comm = new InstructionComm(this.serverIP, Const.PORT, this, returnMsgHandler);
        this.frameSupplier = new FrameSupplier(this, this.comm);
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
                if (GabrielClientActivity.this.comm != null){
                    synchronized (GabrielClientActivity.this.engineInputLock) {
                        GabrielClientActivity.this.engineInput = new EngineInput(frame, parameters);
                        GabrielClientActivity.this.engineInput.notify();
                    }
                }
            }
            mCamera.addCallbackBuffer(frame);
        }
    };


    /**
     * Handles messages passed from streaming threads and result receiving threads.
     */
    private Handler returnMsgHandler = new Handler() {
        public void handleMessage(Message msg) {
            if (msg.what == NetworkProtocol.NETWORK_RET_FAILED) {
                //terminate();
                AlertDialog.Builder builder = new AlertDialog.Builder(GabrielClientActivity.this, AlertDialog.THEME_HOLO_DARK);
                builder.setMessage(msg.getData().getString("message"))
                        .setTitle(R.string.connection_error)
                        .setNegativeButton(R.string.back_button, new DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(DialogInterface dialog, int which) {
                                        GabrielClientActivity.this.finish();
                                    }
                                }
                        )
                        .setCancelable(false);

                AlertDialog dialog = builder.create();
                dialog.show();
            } else if (msg.what == NetworkProtocol.NETWORK_RET_SPEECH) {
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
                subtitleView = (TextView) findViewById(R.id.subtitleText);
                subtitleView.setText(ttsMessage);
            } else if (msg.what == NetworkProtocol.NETWORK_RET_IMAGE) {
                Bitmap feedbackImg = (Bitmap) msg.obj;
                imgView = (ImageView) findViewById(R.id.guidance_image);
                videoView = (VideoView) findViewById(R.id.guidance_video);
                imgView.setVisibility(View.VISIBLE);
                videoView.setVisibility(View.GONE);
                imgView.setImageBitmap(feedbackImg);
            }
        }
    };

    /**
     * Terminates all services.
     */
    private void terminate() {
        Log.v(LOG_TAG, "++terminate");

        isRunning = false;

        // Allow this.backgroundHandler to return if it is currently waiting on this.engineInputLock
        synchronized (this.engineInputLock) {
            this.engineInputLock.notify();
        }

        if (this.backgroundThread != null) {
            this.stopBackgroundThread();
        }

        if (this.comm != null) {
            this.comm.stop();
            this.comm = null;
        }

        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        if (preview != null) {
            preview.close();
            preview = null;
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

        // TODO: Stream audio data
//        while (isAudioRecording) {
//            int n = audioRecorder.read(data, 0, audioBufferSize);
//
//            if (n != AudioRecord.ERROR_INVALID_OPERATION && n > 0) {
//                if (audioStreamingThread != null) {
//                    audioStreamingThread.push(data);
//                }
//            }
//        }
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
