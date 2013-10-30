package com.example.testbatteryrecording;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.IOException;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;
import android.view.Menu;
import android.view.View;
import android.widget.TextView;

public class MainActivity extends Activity {

	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		setContentView(R.layout.activity_main);
		
		/*
		 * Test starting the battery recording service.
		 */
		startBatteryRecording();
		
		
	}

	@Override
	public boolean onCreateOptionsMenu(Menu menu) {
		// Inflate the menu; this adds items to the action bar if it is present.
		getMenuInflater().inflate(R.menu.main, menu);
		return true;
	}

	/*
	 * Test stopping the battery recording service by clicking on the Stop button
	 */
	public void stopRecording(View view) {
		stopBatteryRecording();
	}
	
	
	Intent batteryRecordingService = null;
	/*
	 * Start recording battery info by sending an intent
	 */
	public void startBatteryRecording() {
		Log.i("Main", "Starting Battery Recording Service");
		
        batteryRecordingService = new Intent(this, BatteryRecordingService.class);
        startService(batteryRecordingService);
	}

	/*
	 * Stop recording battery info by sending an intent
	 */
	public void stopBatteryRecording() {
		Log.i("Main", "Stopping Battery Recording Service");
		if (batteryRecordingService != null) {
			stopService(batteryRecordingService);
			batteryRecordingService = null;
		}
	}
}
