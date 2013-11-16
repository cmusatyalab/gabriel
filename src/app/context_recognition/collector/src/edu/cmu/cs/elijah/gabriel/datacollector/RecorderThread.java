package edu.cmu.cs.elijah.gabriel.datacollector;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Vector;

import android.os.Environment;
import android.util.Log;

public class RecorderThread extends Thread {
	private static final String LOG_TAG = "Recorder";
	
	public static final int FILE_TYPE_ACC = 0;
	public static final int FILE_TYPE_GYRO = 1;
	public static final int FILE_TYPE_MAG = 2;
	
	private static final String recordingDir = "SensorCollector";

	private File collectorDir, experimentDir;
	private File accFile, gyroFile, magFile;

	private volatile boolean is_running = false;

	private Vector<SensorData> accDataList = new Vector<SensorData>();
	private Vector<SensorData> gyroDataList = new Vector<SensorData>();
	private Vector<SensorData> magDataList = new Vector<SensorData>();
	
	private long firstStartTime = 0;
    private long currentUpdateTime = 0;
	
	class SensorData{
		public int sentTime;
		public float[] data;
		public SensorData(int time, float[] s) {
			sentTime = time;
			data = s;
		}
	}

	public RecorderThread() {
		this.is_running = false;
		
		collectorDir = null;
		experimentDir = null;
		accFile = null;
		gyroFile = null;
		magFile = null;
	}
	
	private void initFile() {
	    collectorDir = new File(Environment.getExternalStorageDirectory() + File.separator + recordingDir);
        collectorDir.mkdirs();
        
        int expNum = 1;
        experimentDir = new File(collectorDir, "experiment" + expNum);
        while (experimentDir.isDirectory()) {
            expNum++;
            experimentDir = new File(collectorDir, "experiment" + expNum);
        }
        experimentDir.mkdir();
        
        accFile = new File(experimentDir, "acc.txt");
        gyroFile = new File(experimentDir, "gyro.txt");
        magFile = new File(experimentDir, "mag.txt");
        
        Log.i(LOG_TAG, "All files succesfully created");
	}

	public void run() {
		Log.i(LOG_TAG, "Recorder thread running");

		initFile();

		while (this.is_running) {
			try {
				if (this.accDataList.size() == 0 && this.gyroDataList.size() == 0 && this.magDataList.size() == 0){
					try {
						Thread.sleep(10);
//						Log.d(LOG_TAG, "slept for a while because no sensor data received");
					} catch (InterruptedException e) {}
					continue;
				}
				
		        if (this.accDataList.size() > 0) {
//		            Log.v(LOG_TAG, "Write new acc data");
    		        FileWriter writer = new FileWriter(accFile, true);				
    				while (this.accDataList.size() > 0) {
    					SensorData data = this.accDataList.remove(0);
    					writer.write(String.format("%d\t%f\t%f\t%f\n", data.sentTime, data.data[0], data.data[1], data.data[2]));
    				}
    				writer.close();
		        }
		        
		        if (this.gyroDataList.size() > 0) {
                    FileWriter writer = new FileWriter(gyroFile, true);              
                    while (this.gyroDataList.size() > 0) {
                        SensorData data = this.gyroDataList.remove(0);
                        writer.write(String.format("%d\t%f\t%f\t%f\n", data.sentTime, data.data[0], data.data[1], data.data[2]));
                    }
                    writer.close();
                }
		        
		        if (this.magDataList.size() > 0) {
                    FileWriter writer = new FileWriter(magFile, true);              
                    while (this.magDataList.size() > 0) {
                        SensorData data = this.magDataList.remove(0);
                        writer.write(String.format("%d\t%f\t%f\t%f\n", data.sentTime, data.data[0], data.data[1], data.data[2]));
                    }
                    writer.close();
                }
			} catch (IOException e) {
				Log.e(LOG_TAG, "Error writing data: " + e.getMessage());
			}
		}
		this.is_running = false;
	}

	public void push(float[] sensor, int fileType) {
//	    Log.v(LOG_TAG, String.format("Got push message from sensor %d", fileType));
	    
		if (firstStartTime == 0) {
			firstStartTime = System.currentTimeMillis();
		}
		currentUpdateTime = System.currentTimeMillis();
		
		switch (fileType) {
		    case FILE_TYPE_ACC:
		        this.accDataList.add(new SensorData((int)(currentUpdateTime-firstStartTime), sensor));
		        break;
		    case FILE_TYPE_GYRO:
		        this.gyroDataList.add(new SensorData((int)(currentUpdateTime-firstStartTime), sensor));
		        break;
		    case FILE_TYPE_MAG:
		        this.magDataList.add(new SensorData((int)(currentUpdateTime-firstStartTime), sensor));
		        break;
		}
	}
	
	public void startRecording() {
	    is_running = true;
	    Log.i(LOG_TAG, "Recorder running.");
	}
	
	public void stopRecording() {
        is_running = false;
        Log.i(LOG_TAG, "Recorder stopped.");
    }
}
