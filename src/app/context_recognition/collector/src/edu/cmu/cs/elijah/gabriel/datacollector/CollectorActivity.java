package edu.cmu.cs.elijah.gabriel.datacollector;

import android.app.Activity;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.View;
import android.view.View.OnClickListener;
import android.view.WindowManager;
import android.widget.Button;

public class CollectorActivity extends Activity implements SensorEventListener {
    private static final String LOG_TAG = "CollectorMain";
    
    private Button button_start;
    private Button button_stop;
    
    private SensorManager mSensorManager = null;
    private Sensor mAcc = null, mGyro = null, mMag = null;
    
    private RecorderThread recorderThread = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED+
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON+
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        
        setContentView(R.layout.activity_collector);
        
        init();
        
        button_start.setOnClickListener(new OnClickListener(){
            @Override
            public void onClick(View v) {
                startRecording();
                button_start.setEnabled(false);
            }
        });
        
        button_stop.setOnClickListener(new OnClickListener(){
            @Override
            public void onClick(View v) {
                stopRecording();
                button_start.setEnabled(true);
            }
        });
    }
    
    private void init() {
        button_start = (Button)findViewById(R.id.start_button);
        button_stop = (Button)findViewById(R.id.stop_button);
                
        if (mSensorManager == null) {
            mSensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
            mAcc = mSensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            mSensorManager.registerListener(this, mAcc, SensorManager.SENSOR_DELAY_NORMAL);
            mGyro = mSensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE);
            mSensorManager.registerListener(this, mGyro, SensorManager.SENSOR_DELAY_NORMAL);
            mMag = mSensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD);
            mSensorManager.registerListener(this, mMag, SensorManager.SENSOR_DELAY_NORMAL);
        }
    }
        
    private void startRecording() {
        recorderThread = new RecorderThread();
        recorderThread.start();
        recorderThread.startRecording();
    }
    
    private void stopRecording() {
        if ((recorderThread != null) && (recorderThread.isAlive())) {
            recorderThread.stopRecording();
            recorderThread = null;
        }
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        // Inflate the menu; this adds items to the action bar if it is present.
        getMenuInflater().inflate(R.menu.collector, menu);
        return true;
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
        // Don't forget to shutdown!
        if ((recorderThread != null) && (recorderThread.isAlive())) {
            recorderThread.stopRecording();
            recorderThread = null;
        }
        if (mSensorManager != null) {
            mSensorManager.unregisterListener(this);
            mSensorManager = null;
            mAcc = null;
            mGyro = null;
            mMag = null;
        }
        finish();
    }
    
    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        if (recorderThread == null)
            return;
        if (event.sensor.getType() == Sensor.TYPE_ACCELEROMETER) {
            recorderThread.push(event.values, RecorderThread.FILE_TYPE_ACC);
        }
        if (event.sensor.getType() == Sensor.TYPE_GYROSCOPE) {
            recorderThread.push(event.values, RecorderThread.FILE_TYPE_GYRO);
        }
        if (event.sensor.getType() == Sensor.TYPE_MAGNETIC_FIELD) {
            recorderThread.push(event.values, RecorderThread.FILE_TYPE_MAG);
        }
        // Log.d(LOG_TAG, "acc_x : " + mSensorX + "\tacc_y : " + mSensorY);
    }

}
