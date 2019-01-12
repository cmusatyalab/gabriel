package edu.cmu.cs.gabrielclient;

import android.hardware.Sensor;
import android.hardware.SensorManager;
import android.media.AudioRecord;

import java.util.ArrayList;

import edu.cmu.cs.gabrielclient.network.AccStreamingThread;
import edu.cmu.cs.gabrielclient.network.AudioStreamingThread;
import edu.cmu.cs.gabrielclient.network.VideoStreamingThread;
import edu.cmu.cs.gabrielclient.stream.StreamIF;

/**
 * Manages sensor streams
 */
public class SensorStreamManager {
    // singleton class
    private static SensorStreamManager instance = null;
    private ArrayList<StreamIF> streams = new ArrayList<>();

    // sensor data streaming to the server
    // video
    private VideoStreamingThread videoStreamingThread = null;
    // accelerometer
    private AccStreamingThread accStreamingThread = null;
    private SensorManager sensorManager = null;
    private Sensor sensorAcc = null;
    // audio
    private AudioStreamingThread audioStreamingThread = null;
    private AudioRecord audioRecorder = null;
    private Thread audioRecordingThread = null;
    private boolean isAudioRecording = false;
    private int audioBufferSize = -1;

    public static SensorStreamManager getInstance(){
        if (instance == null){
            instance = new SensorStreamManager();
        }
        return instance;
    }

    private SensorStreamManager(){
//        // IMU sensors
//        if (config.ACC) {
//            if (sensorManager == null) {
//                sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
//                sensorAcc = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
//                sensorManager.registerListener(this, sensorAcc, SensorManager.SENSOR_DELAY_NORMAL);
//            }
//        }
//
//        // Audio
//        if (config.AUDIO) {
//            if (audioRecorder == null) {
//                startAudioRecording();
//            }
//        }
    }

    public void addStream(StreamIF s){
        streams.add(s);
    }

    public void startStreaming(){
        for (StreamIF s: streams){
            s.start();
        }
//
//        if (Const.SENSOR_ACC) {
//            accStreamingThread = new AccStreamingThread(serverIP, Const.ACC_STREAM_PORT, callerHandler,
//                    tokenController, logicalTime);
//            accStreamingThread.start();
//        }
//
//        if (Const.SENSOR_AUDIO) {
//            audioStreamingThread = new AudioStreamingThread(serverIP, Const.AUDIO_STREAM_PORT, callerHandler,
//                    tokenController, logicalTime);
//            audioStreamingThread.start();
//        }
    }

    public void stopStreaming(){
        for (StreamIF s: streams){
            s.stop();
        }
//        if ((accStreamingThread != null) && (accStreamingThread.isAlive())) {
//            accStreamingThread.stopStreaming();
//            accStreamingThread = null;
//        }
//        if ((audioStreamingThread != null) && (audioStreamingThread.isAlive())) {
//            audioStreamingThread.stopStreaming();
//            audioStreamingThread = null;
//        }
    }

}
