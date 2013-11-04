package edu.cmu.cs.gabriel;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;

import android.app.IntentService;
import android.content.Intent;
import android.os.Environment;
import android.os.Handler;
import android.util.Log;

public class BatteryRecordingService extends IntentService {
	FileWriter mFileWriter = null;
	String mOutputFileName = "batteryRecording";
	String mAppName = "SampleApp";	
	boolean stopped = false;
	/** 
	 * A constructor is required, and must call the super IntentService(String)
	 * constructor with a name for the worker thread.
	 */
	public BatteryRecordingService(/*Handler handler*/) {
	    super("BatteryRecordingService");
	    //mHandler = handler;
	    stopped = false;
	  }
	  
	@Override
	protected void onHandleIntent(Intent arg0) {
		Log.i("BatteryRecordingService", "Got Intent, starting to record");
		// TODO Auto-generated method stub
		try {
			File dir = new File(Environment.getExternalStorageDirectory() + File.separator + mAppName + File.separator);
			dir.mkdirs();
			
			File file = new File(Environment.getExternalStorageDirectory() + File.separator + mAppName + File.separator + mOutputFileName);
			Log.i("BatteryRecordingService", file.getAbsolutePath());
			mFileWriter = new FileWriter(file);//,true); //Append
			mFileWriter.write("Time/ms\tCurrent/mA\tVoltage/V\n");
			mFileWriter.close();
			
			int TotalTimes = 400;
			int count = 0;
			while ((!stopped) && (count < TotalTimes))
			{
				try {
					Thread.sleep(100);
					checkBattery();
				}
				catch (InterruptedException ex) {
				}
				
				//Comment out this for infinite loop
				//count ++;
				
			}
			
			//mFileWriter.close();
			Log.i("Battery", "BatteryRecordingService properly stopped.");
		}
		catch (IOException ex)
		{
			Log.e("Battery", "Output File", ex);
			return;
		}
	}
	@Override
	public void onDestroy() {
		Log.i("Battery", "Setting flag 'stopped' in onDestroy()");
		stopped = true;
	}
	
	enum PhoneModel {
		GoogleGlassBlue,
		SamsungGalaxyS2, //Not working
		SamsungGalaxyNote2 //Not working
	}
	
	Handler mHandler = null;
	public void checkBattery() {
		int current=0;
		int voltage=0;
		try {
			String BatteryInfoPath = null;
			String CurrentFileName = null;
			String VoltageFileName = null;

			PhoneModel currentModel = PhoneModel.GoogleGlassBlue;
			//PhoneModel currentModel = PhoneModel.SamsungGalaxyNote2;
			
			switch (currentModel) {
			
			case GoogleGlassBlue:
				BatteryInfoPath = "/sys/class/power_supply/bq27520-0/";
				CurrentFileName = "current_now";
				VoltageFileName = "voltage_now";
				break;
				
			case SamsungGalaxyS2:
				BatteryInfoPath = "/sys/class/power_supply/battery/"; 
				CurrentFileName = "batt_current_now";
				//CurrentFileName = "current_avg";
				VoltageFileName = "voltage_now";
				
			case SamsungGalaxyNote2:
				BatteryInfoPath = "/sys/class/power_supply/battery/"; 
				CurrentFileName = "current_now";
				VoltageFileName = "voltage_now";
				
			default:
				
			};
			
			File f = new File(BatteryInfoPath + CurrentFileName);
	        if (f.exists())
	        {
	        	FileReader reader = new FileReader(f);
	        	BufferedReader br = new BufferedReader(reader);
	        	current = Integer.parseInt(br.readLine());
	            reader.close();
	        }
	    
	        f = new File(BatteryInfoPath + VoltageFileName);
	        if (f.exists())
	        {
	        	FileReader reader = new FileReader(f);
	        	BufferedReader br = new BufferedReader(reader);
	        	voltage = Integer.parseInt(br.readLine());
	            reader.close();
	        }
            Log.i("Battery", "Current : " + current + "\t" + "Voltage : " + voltage);
            long time = System.currentTimeMillis();

			File file = new File(Environment.getExternalStorageDirectory() + File.separator + mAppName + File.separator + mOutputFileName);
			mFileWriter = new FileWriter(file,true); //Append
			String BatteryInfo = time + "\t" + current/1000.0 + "\t" + voltage/1000000.0 + "\n"; 
            mFileWriter.write(BatteryInfo);
            mFileWriter.flush();
			mFileWriter.close();
		}
		catch (FileNotFoundException ex)
		{
			Log.e("Battery", "File not found!", ex);
		}
		catch (IOException ex)
		{
			Log.e("Battery", "Why IOException?", ex);
		}
	}

}
