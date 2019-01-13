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

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Timer;
import java.util.TimerTask;

import edu.cmu.cs.gabrielclient.control.ControlThread;
import edu.cmu.cs.gabrielclient.network.LogicalTime;
import edu.cmu.cs.gabrielclient.network.NetworkProtocol;
import edu.cmu.cs.gabrielclient.stream.ResultStream;
import edu.cmu.cs.gabrielclient.stream.StreamIF;
import edu.cmu.cs.gabrielclient.stream.VideoStream;
import edu.cmu.cs.gabrielclient.token.TokenController;
import edu.cmu.cs.gabrielclient.util.PingThread;
import edu.cmu.cs.gabrielclient.util.ResourceMonitoringService;

public class GabrielClientActivity extends Activity {

    private static final String LOG_TAG = GabrielClientActivity.class.getSimpleName();
    // general set up
    private String serverIP = null;
    private boolean isRunning = false;
    private boolean isFirstExperiment = true;
    private SensorStreamManager streamManager;
    // activity views
    private CameraPreview preview = null;
    private ImageView imgView = null;
    private VideoView videoView = null;
    private TextView subtitleView = null;
    private InstructionViewer iv = null;
    // controllers
    private ControlThread controlThread = null;
    private TokenController tokenController = null;
    private FileWriter controlLogWriter = null;
    private PingThread pingThread = null;
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
        // iv initializes tts and mediaControl, therefore needs to be in onCreate
        iv = new InstructionViewer(this.getApplicationContext(), imgView, videoView, subtitleView);
        tokenController = new TokenController(Const.TOKEN_SIZE, null);
        streamManager = SensorStreamManager.getInstance();
    }

    @Override
    protected void onResume() {
        Log.v(LOG_TAG, "++onResume");
        super.onResume();
        preview = findViewById(R.id.camera_preview);
        preview.start(CameraPreview.CameraConfiguration.getInstance(), VideoStream.previewCallback);

        stopControllers();
        stopDataStreams();
        initPersistentStorage();
        initControl();
        initDataStreams();

        // Monitor mobile resources (CPU and power)
        if (Const.MONITOR_RESOURCE) {
            startResourceMonitoring();
        }
        isRunning = true;

        if (Const.IS_EXPERIMENT) { // experiment mode
//            runExperiments();
        } else { // demo mode
//            initPerRun(serverIP, Const.TOKEN_SIZE, null);
//            serverIP = Const.SERVER_IP;
        }
    }

    private void stopDataStreams() {
        streamManager.stopStreaming();
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

    private void initViews() {
        imgView = findViewById(R.id.guidance_image);
        videoView = findViewById(R.id.guidance_video);
        subtitleView = findViewById(R.id.subtitleText);
        if (Const.SHOW_SUBTITLES) {
            findViewById(R.id.subtitleText).setVisibility(View.VISIBLE);
        }
    }

    private void initPersistentStorage() {
        Const.ROOT_DIR.mkdirs();
        Const.EXP_DIR.mkdirs();
        if (Const.SAVE_FRAME_SEQUENCE) {
            Const.SAVE_FRAME_SEQUENCE_DIR.mkdirs();
        }
    }

    private void initDataStreams() {
        resultStream = new ResultStream(new StreamIF.StreamConfig(Const.SERVER_IP, Const
                .RESULT_RECEIVING_PORT,
                tokenController, uiHandler, null));
        streamManager.addStream(resultStream);
        if (Const.SENSOR_VIDEO) {
            VideoStream vs = new VideoStream(new StreamIF.StreamConfig(Const.SERVER_IP, Const
                    .VIDEO_STREAM_PORT,
                    tokenController, uiHandler, null));
            streamManager.addStream(vs);
//            preview.start(CameraPreview.CameraConfiguration.getInstance(), vs.previewCallback);
        }
        streamManager.startStreaming();
    }

    private void stopControllers() {
        if ((controlThread != null) && (controlThread.isAlive())) {
            controlThread.close();
            controlThread = null;
        }
        if (tokenController != null) {
            tokenController.close();
        }
        if (resultStream != null) {
            resultStream.stop();
            resultStream = null;
        }
        if ((pingThread != null) && (pingThread.isAlive())) {
            pingThread.kill();
            pingThread.interrupt();
            pingThread = null;
        }
    }

    private void initControl() {
        controlThread = new ControlThread(Const.SERVER_IP, Const.CONTROL_PORT, uiHandler,
                tokenController);
        controlThread.start();

        if (Const.BACKGROUND_PING) {
            pingThread = new PingThread(serverIP, Const.PING_INTERVAL);
            pingThread.start();
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
     * Does initialization before each run (connecting to a specific server).
     * Called once before each experiment.
     */
    private void initPerRun(String serverIP, int tokenSize, File latencyFile) {
        Log.v(LOG_TAG, "++initPerRun");

        if (Const.IS_EXPERIMENT) {
            if (isFirstExperiment) {
                isFirstExperiment = false;
            } else {
                try {
                    Thread.sleep(20 * 1000);
                } catch (InterruptedException e) {
                }
                controlThread.sendControlMsg("ping");
                // wait a while for ping to finish...
                try {
                    Thread.sleep(5 * 1000);
                } catch (InterruptedException e) {
                }
            }
        }
        if ((controlThread != null) && (controlThread.isAlive())) {
            controlThread.close();
            controlThread = null;
        }

        if (serverIP == null) return;

        if (Const.IS_EXPERIMENT) {
            try {
                controlLogWriter = new FileWriter(Const.CONTROL_LOG_FILE);
            } catch (IOException e) {
                Log.e(LOG_TAG, "Control log file cannot be properly opened", e);
            }
        }


        if (Const.IS_EXPERIMENT) {
            controlThread.sendControlMsg("ping");
            // wait a while for ping to finish...
            try {
                Thread.sleep(5 * 1000);
            } catch (InterruptedException e) {
            }
        }

    }


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
        stopControllers();

//        if ((accStreamingThread != null) && (accStreamingThread.isAlive())) {
//            accStreamingThread.stopStreaming();
//            accStreamingThread = null;
//        }
//        if ((audioStreamingThread != null) && (audioStreamingThread.isAlive())) {
//            audioStreamingThread.stopStreaming();
//            audioStreamingThread = null;
//        }
//        if (preview != null) {
//            preview.close();
//            preview = null;
//        }
//        if (sensorManager != null) {
//            sensorManager.unregisterListener(this);
//            sensorManager = null;
//            sensorAcc = null;
//        }
//        if (audioRecorder != null) {
//            stopAudioRecording();
//        }
        stopResourceMonitoring();
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
//                    if (preview == null) {
//                        preview = findViewById(R.id.camera_preview);
//                        preview.start(CameraPreview.CameraConfiguration.getInstance(),
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
//                    if (preview != null) {
//                        preview.close();
//                        preview = null;
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
//            if (preview != null) {
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
//                preview.close();
//                preview.start(CameraPreview.CameraConfiguration.getInstance(), previewCallback);
//            }
//
//        } catch (JSONException e) {
//            Log.e(LOG_TAG, "" + msgJSON);
//            Log.e(LOG_TAG, "error in processing server control messages" + e);
//            return;
//        }
//    }

    /**************** Battery recording *************************/
    /*
     * Resource monitoring of the mobile device
     * Checks battery and CPU usage, as well as device temperature
     */
    public void startResourceMonitoring() {
        Log.i(LOG_TAG, "Starting Battery Recording Service");
        resourceMonitoringIntent = new Intent(this, ResourceMonitoringService.class);
        startService(resourceMonitoringIntent);
    }

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
    public void stopResourceMonitoring() {
        Log.i(LOG_TAG, "Stopping Battery Recording Service");
        if (resourceMonitoringIntent != null) {
            stopService(resourceMonitoringIntent);
            resourceMonitoringIntent = null;
        }
    }

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
                        iv.parseAndSetInstruction(inst);
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
