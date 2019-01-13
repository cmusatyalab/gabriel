package edu.cmu.cs.gabrielclient;

import android.hardware.Sensor;
import android.hardware.SensorManager;
import android.media.AudioRecord;

import java.util.ArrayList;

import edu.cmu.cs.gabrielclient.network.AccStreamingThread;
import edu.cmu.cs.gabrielclient.network.AudioStreamingThread;
import edu.cmu.cs.gabrielclient.network.VideoStreamingThread;
import edu.cmu.cs.gabrielclient.util.LifeCycleIF;


/**
 * Manages sensor streams
 */
public class LifeCycleManager implements LifeCycleIF {
    // singleton class
    private static LifeCycleManager instance = null;
    private ArrayList<LifeCycleIF> items = new ArrayList<>();

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

    private LifeCycleManager() {
//        // IMU sensors
//        if (config.ACC) {
//            if (sensorManager == null) {
//                sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
//                sensorAcc = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
//                sensorManager.registerListener(this, sensorAcc, SensorManager
// .SENSOR_DELAY_NORMAL);
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

    public static LifeCycleManager getInstance() {
        if (instance == null) {
            instance = new LifeCycleManager();
        }
        return instance;
    }

    public void add(LifeCycleIF s) {
        items.add(s);
    }

    @Override
    public void onResume() {
        for (LifeCycleIF s : items) {
            s.onResume();
        }
    }

    @Override
    public void onPause() {
        for (LifeCycleIF s : items) {
            s.onPause();
        }
    }

    @Override
    public void onDestroy() {
        for (LifeCycleIF s : items) {
            s.onDestroy();
        }
    }
}
