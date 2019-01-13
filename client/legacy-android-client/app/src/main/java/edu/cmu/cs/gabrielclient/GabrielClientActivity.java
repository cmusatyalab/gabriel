package edu.cmu.cs.gabrielclient;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.VideoView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.FileWriter;
import java.io.IOException;
import java.util.Timer;
import java.util.TimerTask;

import edu.cmu.cs.gabrielclient.control.CameraPreviewController;
import edu.cmu.cs.gabrielclient.control.InstructionViewController;
import edu.cmu.cs.gabrielclient.control.ResourceMonitorController;
import edu.cmu.cs.gabrielclient.control.ServerController;
import edu.cmu.cs.gabrielclient.control.StorageController;
import edu.cmu.cs.gabrielclient.control.TokenController;
import edu.cmu.cs.gabrielclient.network.ConnectionConfig;
import edu.cmu.cs.gabrielclient.network.LogicalTime;
import edu.cmu.cs.gabrielclient.network.NetworkProtocol;
import edu.cmu.cs.gabrielclient.stream.ResultStream;
import edu.cmu.cs.gabrielclient.stream.VideoStream;

public class GabrielClientActivity extends Activity {

    private static final String LOG_TAG = GabrielClientActivity.class.getSimpleName();
    // general set up
    private String serverIP = null;
    private boolean isRunning = false;
    private boolean isFirstExperiment = true;
    private LifeCycleManager lifeCycleManager;
    // activity views
    private CameraPreview cameraPreview = null;
    private ImageView imgView = null;
    private VideoView videoView = null;
    private TextView subtitleView = null;
    // controllers: controlThread, tokenController, and instruction view controller
    private ServerController serverController = null;
    private InstructionViewController ivController = null;
    private TokenController tokenController = null;
    private FileWriter controlLogWriter = null;
    // handling results from server
    private ResultStream resultStream = null;
    // measurements
    private LogicalTime logicalTime = null;
    private Intent resourceMonitoringIntent = null;
    // Handles messages passed from streaming threads and result receiving threads.
    private Handler uiHandler = new UIThreadHandler();


    @Override
    protected void onCreate(Bundle savedInstanceState) {
        Log.v(LOG_TAG, "++onCreate");
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED +
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON +
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        initViews();
        lifeCycleManager = LifeCycleManager.getInstance();
        // initialize controllers
        // TODO (junjuew): add background ping thread
        CameraPreviewController cpc = new CameraPreviewController(cameraPreview);
        lifeCycleManager.add(cpc);
        ivController = new InstructionViewController(this.getApplicationContext(), imgView,
                videoView,
                subtitleView);
        lifeCycleManager.add(ivController);
        tokenController = new TokenController(Const.TOKEN_SIZE, null);
        lifeCycleManager.add(tokenController);
        serverController = new ServerController(new ConnectionConfig(Const.SERVER_IP, Const
                .CONTROL_PORT,
                tokenController, uiHandler, null));
        lifeCycleManager.add(serverController);
        StorageController sc = new StorageController();
        lifeCycleManager.add(sc);
        if (Const.MONITOR_RESOURCE) {
            ResourceMonitorController rmc = new ResourceMonitorController(this
                    .getApplicationContext());
            lifeCycleManager.add(rmc);
        }
        // initialize data streams
        // TODO(junjuew): add audio and sensor data streams
        resultStream = new ResultStream(new ConnectionConfig(Const.SERVER_IP, Const
                .RESULT_RECEIVING_PORT,
                tokenController, uiHandler, null));
        lifeCycleManager.add(resultStream);
        if (Const.SENSOR_VIDEO) {
            VideoStream vs = new VideoStream(new ConnectionConfig(Const.SERVER_IP, Const
                    .VIDEO_STREAM_PORT,
                    tokenController, uiHandler, null));
            lifeCycleManager.add(vs);
        }
    }

    @Override
    protected void onResume() {
        Log.v(LOG_TAG, "++onResume");
        super.onResume();
        lifeCycleManager.onResume();
        isRunning = true;

//        if (Const.IS_EXPERIMENT) { // experiment mode
//            runExperiments();
//        } else { // demo mode
//            initPerRun(serverIP, Const.TOKEN_SIZE, null);
//            serverIP = Const.SERVER_IP;
//        }
    }

    @Override
    protected void onPause() {
        Log.v(LOG_TAG, "++onPause");
        lifeCycleManager.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        Log.v(LOG_TAG, "++onDestroy");
        lifeCycleManager.onDestroy();
        super.onDestroy();
    }

    private void initViews() {
        imgView = findViewById(R.id.guidance_image);
        videoView = findViewById(R.id.guidance_video);
        subtitleView = findViewById(R.id.subtitleText);
        cameraPreview = findViewById(R.id.camera_preview);
        if (Const.SHOW_SUBTITLES) {
            findViewById(R.id.subtitleText).setVisibility(View.VISIBLE);
        }
    }

    private void networkReconfig(Message msg) {
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
                                if (Const.SYNC_BASE.equals("video")) {
                                    logicalTime.increaseImageTime((int) (delay * 11.5 / 1000));
                                    // in the
                                    // recorded data set, FPS is roughly 11
                                } else if (Const.SYNC_BASE.equals("acc")) {
                                    logicalTime.increaseAccTime(delay);
                                }
//                                        processServerControl(controlJSON);
                            }
                        });
                    }
                };

                // sensor control at a delayed time
                controlTimer.schedule(controlTask, delay);
            } else {
//                        processServerControl(controlJSON);
            }
        } catch (JSONException e) {
            Log.e(LOG_TAG, "error in jsonizing server control messages" + e);
        }
    }

    private void showAlert(Message msg) {
        AlertDialog.Builder builder = new AlertDialog.Builder(this, AlertDialog
                .THEME_HOLO_DARK);
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
    }


    /**
     * Does initialization before each run (connecting to a specific server).
     * Called once before each experiment.
     */
//    private void initPerRun(String serverIP, int tokenSize, File latencyFile) {
//        Log.v(LOG_TAG, "++initPerRun");
//
//        if (Const.IS_EXPERIMENT) {
//            if (isFirstExperiment) {
//                isFirstExperiment = false;
//            } else {
//                try {
//                    Thread.sleep(20 * 1000);
//                } catch (InterruptedException e) {
//                }
//                controlThread.sendControlMsg("ping");
//                // wait a while for ping to finish...
//                try {
//                    Thread.sleep(5 * 1000);
//                } catch (InterruptedException e) {
//                }
//            }
//        }
//        if ((controlThread != null) && (controlThread.isAlive())) {
//            controlThread.close();
//            controlThread = null;
//        }
//
//        if (serverIP == null) return;
//
//        if (Const.IS_EXPERIMENT) {
//            try {
//                controlLogWriter = new FileWriter(Const.CONTROL_LOG_FILE);
//            } catch (IOException e) {
//                Log.e(LOG_TAG, "Control log file cannot be properly opened", e);
//            }
//        }
//
//
//        if (Const.IS_EXPERIMENT) {
//            controlThread.sendControlMsg("ping");
//            // wait a while for ping to finish...
//            try {
//                Thread.sleep(5 * 1000);
//            } catch (InterruptedException e) {
//            }
//        }
//
//    }


    /**
     * Runs a set of experiments with different server IPs and token numbers.
     * IP list and token sizes are defined in the Const file.
     */
//    private void runExperiments() {
//        final Timer startTimer = new Timer();
//        TimerTask autoStart = new TimerTask() {
//            int ipIndex = 0;
//            int tokenIndex = 0;
//
//            @Override
//            public void run() {
//                GabrielClientActivity.this.runOnUiThread(new Runnable() {
//                    @Override
//                    public void run() {
//                        // end condition
//                        if ((ipIndex == Const.SERVER_IP_LIST.length) || (tokenIndex == Const
// .TOKEN_SIZE_LIST
// .length)) {
//                            Log.i(LOG_TAG, "Finish all experiemets");
//
//                            // initPerRun(null, 0, null); // just to get another set of ping
//                            // results
//
//                            startTimer.cancel();
//                            terminate();
//                            return;
//                        }
//
//                        // make a new configuration
//                        serverIP = Const.SERVER_IP_LIST[ipIndex];
//                        int tokenSize = Const.TOKEN_SIZE_LIST[tokenIndex];
//                        File latencyFile = new File(Const.EXP_DIR.getAbsolutePath() + File
// .separator +
//                                "latency-" + serverIP + "-" + tokenSize + ".txt");
//                        Log.i(LOG_TAG, "Start new experiment - IP: " + serverIP + "\tToken: " +
// tokenSize);
//
//                        // run the experiment
//                        initPerRun(serverIP, tokenSize, latencyFile);
//
//                        Log.i(LOG_TAG, "Initialized a new experiment");
//
//                        // move to the next experiment
//                        tokenIndex++;
//                        if (tokenIndex == Const.TOKEN_SIZE_LIST.length) {
//                            tokenIndex = 0;
//                            ipIndex++;
//                        }
//                    }
//                });
//            }
//        };
//
//        // run 5 minutes for each experiment
//        // startTimer.schedule(autoStart, 1000, 5*60*1000);
//        startTimer.schedule(autoStart, 1000);
//    }

    /**
     * Terminates all services.
     */
    private void terminate() {
        Log.v(LOG_TAG, "++terminate");

        isRunning = false;

//        if ((accStreamingThread != null) && (accStreamingThread.isAlive())) {
//            accStreamingThread.stopStreaming();
//            accStreamingThread = null;
//        }
//        if ((audioStreamingThread != null) && (audioStreamingThread.isAlive())) {
//            audioStreamingThread.stopStreaming();
//            audioStreamingThread = null;
//        }
//        if (sensorManager != null) {
//            sensorManager.unregisterListener(this);
//            sensorManager = null;
//            sensorAcc = null;
//        }
//        if (audioRecorder != null) {
//            stopAudioRecording();
//        }
        if (Const.IS_EXPERIMENT) {
            try {
                controlLogWriter.close();
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in closing control log file");
            }
        }
    }

//    private void processServerControl(JSONObject msgJSON) {
//        if (Const.IS_EXPERIMENT) {
//            try {
//                controlLogWriter.write("" + logicalTime.imageTime + "\n");
//                String log = msgJSON.toString();
//                controlLogWriter.write(log + "\n");
//            } catch (IOException e) {
//            }
//        }
//
//        try {
//            // Switching on/off image sensor
//            if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_IMAGE)) {
//                boolean sw = msgJSON.getBoolean(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_IMAGE);
//                if (sw) { // turning on
//                    Const.SENSOR_VIDEO = true;
//                    tokenController.reset();
//                    if (cameraPreview == null) {
//                        cameraPreview = findViewById(R.id.camera_preview);
//                        cameraPreview.start(CameraPreview.CameraConfiguration.getInstance(),
// previewCallback);
//                    }
//                    if (videoStreamingThread == null) {
//                        videoStreamingThread = new VideoStreamingThread(serverIP, Const
// .VIDEO_STREAM_PORT,
//                                callerHandler, tokenController, logicalTime);
//                        videoStreamingThread.start();
//                    }
//                } else { // turning off
//                    Const.SENSOR_VIDEO = false;
//                    if (cameraPreview != null) {
//                        cameraPreview.close();
//                        cameraPreview = null;
//                    }
//                    if (videoStreamingThread != null) {
//                        videoStreamingThread.stopStreaming();
//                        videoStreamingThread = null;
//                    }
//                }
//            }
//
//            // Switching on/off ACC sensor
//            if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_ACC)) {
//                boolean sw = msgJSON.getBoolean(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_ACC);
//                if (sw) { // turning on
//                    Const.SENSOR_ACC = true;
//                    if (sensorManager == null) {
//                        sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
//                        sensorAcc = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
//                        sensorManager.registerListener(this, sensorAcc, SensorManager
// .SENSOR_DELAY_NORMAL);
//                    }
//                    if (accStreamingThread == null) {
//                        accStreamingThread = new AccStreamingThread(serverIP, Const
// .ACC_STREAM_PORT,
//                                callerHandler, tokenController, logicalTime);
//                        accStreamingThread.start();
//                    }
//                } else { // turning off
//                    Const.SENSOR_ACC = false;
//                    if (sensorManager != null) {
//                        sensorManager.unregisterListener(this);
//                        sensorManager = null;
//                        sensorAcc = null;
//                    }
//                    if (accStreamingThread != null) {
//                        accStreamingThread.stopStreaming();
//                        accStreamingThread = null;
//                    }
//                }
//            }
//            // Switching on/off audio sensor
//            if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_AUDIO)) {
//                boolean sw = msgJSON.getBoolean(NetworkProtocol.SERVER_CONTROL_SENSOR_TYPE_AUDIO);
//                if (sw) { // turning on
//                    Const.SENSOR_AUDIO = true;
//                    if (audioRecorder == null) {
//                        startAudioRecording();
//                    }
//                    if (audioStreamingThread == null) {
//                        audioStreamingThread = new AudioStreamingThread(serverIP, Const
// .AUDIO_STREAM_PORT,
//                                callerHandler, tokenController, logicalTime);
//                        audioStreamingThread.start();
//                    }
//                } else { // turning off
//                    Const.SENSOR_AUDIO = false;
//                    if (audioRecorder != null) {
//                        stopAudioRecording();
//                    }
//                    if (audioStreamingThread != null) {
//                        audioStreamingThread.stopStreaming();
//                        audioStreamingThread = null;
//                    }
//                }
//            }
//
//            // Camera configs
//            if (cameraPreview != null) {
//                CameraPreview.CameraConfiguration camConfig = CameraPreview.CameraConfiguration
// .getInstance();
//                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_FPS))
//                    camConfig.fps = msgJSON.getInt(NetworkProtocol.SERVER_CONTROL_FPS);
//                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_IMG_WIDTH))
//                    camConfig.imgWidth = msgJSON.getInt(NetworkProtocol.SERVER_CONTROL_IMG_WIDTH);
//                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_IMG_HEIGHT))
//                    camConfig.imgHeight = msgJSON.getInt(NetworkProtocol
// .SERVER_CONTROL_IMG_HEIGHT);
//                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_FOCUS)) {
//                    throw new UnsupportedOperationException("FOCUS adjustment is not yet
// supported, but easy to add
// . " +
//                            "See FLASHLIGHT on how to add support.");
//                }
//                if (msgJSON.has(NetworkProtocol.SERVER_CONTROL_FLASHLIGHT)) {
//                    boolean flashlightOn = msgJSON.getBoolean(NetworkProtocol
// .SERVER_CONTROL_FLASHLIGHT);
//                    if (flashlightOn) {
//                        camConfig.flashMode = Camera.Parameters.FLASH_MODE_TORCH;
//                    } else {
//                        camConfig.flashMode = Camera.Parameters.FLASH_MODE_OFF;
//                    }
//                }
//                cameraPreview.close();
//                cameraPreview.start(CameraPreview.CameraConfiguration.getInstance(), previewCallback);
//            }
//
//        } catch (JSONException e) {
//            Log.e(LOG_TAG, "" + msgJSON);
//            Log.e(LOG_TAG, "error in processing server control messages" + e);
//            return;
//        }
//    }

    /**************** SensorEventListener ***********************/
//    @Override
//    public void onAccuracyChanged(Sensor sensor, int accuracy) {
//    }

    /**************** End of SensorEventListener ****************/

//    @Override
//    public void onSensorChanged(SensorEvent event) {
//        /*
//         * Currently only ACC sensor is supported
//         */
//        if (event.sensor.getType() != Sensor.TYPE_ACCELEROMETER)
//            return;
//        if (accStreamingThread != null) {
//            accStreamingThread.push(event.values);
//        }
//    }
    /**************** End of TextToSpeech.OnInitListener ********/


    /**************** Audio recording ***************************/
//    private void startAudioRecording() {
//        audioBufferSize = AudioRecord.getMinBufferSize(Const.RECORDER_SAMPLERATE, Const
// .RECORDER_CHANNELS, Const
//                .RECORDER_AUDIO_ENCODING);
//        Log.d(LOG_TAG, "buffer size of audio recording: " + audioBufferSize);
//        audioRecorder = new AudioRecord(MediaRecorder.AudioSource.MIC,
//                Const.RECORDER_SAMPLERATE, Const.RECORDER_CHANNELS,
//                Const.RECORDER_AUDIO_ENCODING, audioBufferSize);
//        audioRecorder.startRecording();
//
//        isAudioRecording = true;
//
//        audioRecordingThread = new Thread(new Runnable() {
//            @Override
//            public void run() {
//                readAudioData();
//            }
//        }, "AudioRecorder Thread");
//        audioRecordingThread.start();
//    }
//
//    private void readAudioData() {
//        byte data[] = new byte[audioBufferSize];
//
//        while (isAudioRecording) {
//            int n = audioRecorder.read(data, 0, audioBufferSize);
//
//            if (n != AudioRecord.ERROR_INVALID_OPERATION && n > 0) {
//                if (audioStreamingThread != null) {
//                    audioStreamingThread.push(data);
//                }
//            }
//        }
//    }
//
//    /**************** End of audio recording ********************/
//
//    private void stopAudioRecording() {
//        isAudioRecording = false;
//        if (audioRecorder != null) {
//            if (audioRecorder.getState() == AudioRecord.STATE_INITIALIZED)
//                audioRecorder.stop();
//            audioRecorder.release();
//            audioRecorder = null;
//            audioRecordingThread = null;
//        }
//    }

    private class UIThreadHandler extends Handler {
        @Override
        public void handleMessage(Message msg) {
            switch (msg.what) {
                case NetworkProtocol.NETWORK_CONNECT_FAILED:
                    showAlert(msg);
                    break;
                case NetworkProtocol.NETWORK_RET_MESSAGE:
                    String inst = resultStream.parseReturnMsg((String) msg.obj);
                    if (inst != null) {
                        ivController.parseAndSetInstruction(inst);
                    }
                    break;
                case NetworkProtocol.NETWORK_RET_CONFIG:
                    networkReconfig(msg);
                    break;
                default:
                    Log.e(LOG_TAG, "unrecognized message type to UI: " + msg.what);
            }
        }
    }
    /**************** End of battery recording ******************/
}
