package edu.cmu.cs.gabrielclient.util;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;

import android.app.IntentService;
import android.content.Intent;
import android.util.Log;

import edu.cmu.cs.gabrielclient.Const;

public class ResourceMonitoringService extends IntentService {

    private static final String LOG_TAG = "ResourceMonitoring";

    FileWriter mBatteryFileWriter = null;
    FileWriter mCpuFileWriter = null;
    static String mBatteryFileName = "batteryRecording.txt";
    static String mCpuFileName = "cpuRecording.txt";

    boolean stopped = false;
    private Object lock = new Object();
    /**
     * A constructor is required, and must call the super IntentService(String)
     * constructor with a name for the worker thread.
     */
    public ResourceMonitoringService() {
        super("ResourceMonitoringService");
        stopped = false;
    }

    public static void setOutputFileName(String outputFileName) {
        mBatteryFileName = outputFileName;
    }

    public static void setOutputFileNames(String batteryFileName, String cpuFileName) {
        mBatteryFileName = batteryFileName;
        mCpuFileName = cpuFileName;
    }

    @Override
    protected void onHandleIntent(Intent arg0) {
        Log.i(LOG_TAG, "Got Intent, starting to record");
        try {
            if (mBatteryFileName != null) {
                File batteryFile = new File(Const.ROOT_DIR.getAbsolutePath() + File.separator + mBatteryFileName);
                Log.i(LOG_TAG, "battery monitoring file path: " + batteryFile.getAbsolutePath());

                mBatteryFileWriter = new FileWriter(batteryFile); //New Empty File
//			      mBatteryFileWriter.write("Time/ms\tCurrent/mA\tVoltage/V\n");
//                mBatteryFileWriter.close();
            }
            if (mCpuFileName != null) {
                File cpuFile = new File(Const.ROOT_DIR.getAbsolutePath() + File.separator + mCpuFileName);
                Log.i(LOG_TAG, "CPU monitoring file path: " + cpuFile.getAbsolutePath());

                mCpuFileWriter = new FileWriter(cpuFile); //New Empty File
            }

            while ((!stopped)) {
                try {
                    Thread.sleep(100);
                    if (mCpuFileName != null) {
                        checkCpu();
                    }
                    if (mBatteryFileName != null) {
                        checkBattery();
                    }
                }
                catch (InterruptedException e) {}
            }

            Log.i(LOG_TAG, "ResourceMonitoringService properly stopped.");
        }
        catch (IOException e) {
            Log.e(LOG_TAG, "Error in operating files: " + e);
            return;
        }
    }

    @Override
    public void onDestroy() {
        Log.i(LOG_TAG, "++onDestroy");
        stopped = true;

        if (mCpuFileWriter != null){
            try {
                mCpuFileWriter.close();
            } catch (IOException e) {}
            mCpuFileWriter = null;
        }

        if (mBatteryFileWriter != null){
            try {
                mBatteryFileWriter.close();
            } catch (IOException e) {}
            mBatteryFileWriter = null;
        }
    }

    private void checkCpu() {
        try {
            float cpuFreq = 0;
            int temperature = 0;

            String frequencyFileName = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq";
            String temperatureFileName = "/sys/class/power_supply/battery/temp";
            File frequencyFile = new File(frequencyFileName);
            File temperatureFile = new File(temperatureFileName);

            if (frequencyFile.exists()) {
                FileReader reader = new FileReader(frequencyFile);
                BufferedReader br = new BufferedReader(reader);
                cpuFreq = Float.parseFloat(br.readLine());
                reader.close();
            }
            if (temperatureFile.exists()) {
                FileReader reader = new FileReader(temperatureFile);
                BufferedReader br = new BufferedReader(reader);
                temperature = Integer.parseInt(br.readLine());
                reader.close();
            }

            long time = System.currentTimeMillis();
            String cpuInfo = time + "\t" + cpuFreq + "\t" + temperature + "\n";

//            File file = new File(Const.ROOT_DIR.getAbsolutePath() + File.separator + mCpuFileName);
//            mCpuFileWriter = new FileWriter(file, true); //Append
            if (mCpuFileWriter != null) {
                mCpuFileWriter.write(cpuInfo);
            }
//            mCpuFileWriter.close();
//            Log.v(LOG_TAG, cpuInfo);
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in operating files: " + e);
        }

    }

    public void checkBattery() {
        try {
            int current = 0;
            int voltage = 0;

            String BatteryInfoPath = null, CurrentFileName = null, VoltageFileName = null;

            switch (Const.deviceModel) {

                case GoogleGlass:
                    BatteryInfoPath = "/sys/class/power_supply/bq27520-0/";
                    CurrentFileName = "current_now";
                    VoltageFileName = "voltage_now";
                    break;

                default:
                    BatteryInfoPath = "/sys/class/power_supply/battery/";
                    CurrentFileName = "current_now";
                    VoltageFileName = "voltage_now";
            };

            File f = new File(BatteryInfoPath + CurrentFileName);
            if (f.exists())
            {
                FileReader reader = new FileReader(f);
                BufferedReader br = new BufferedReader(new FileReader(f));
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

            long time = System.currentTimeMillis();

//            File file = new File(Const.ROOT_DIR.getAbsolutePath() + File.separator + mBatteryFileName);
//            mBatteryFileWriter = new FileWriter(file, true); //Append
            String batteryInfo = time + "\t" + current/1000.0 + "\t" + voltage/1000000.0 + "\n";
//            Log.v(LOG_TAG, batteryInfo);
            if (mBatteryFileWriter != null) {
                mBatteryFileWriter.write(batteryInfo);
            }
//            mBatteryFileWriter.flush();
//            mBatteryFileWriter.close();
        }
        catch (IOException e) {
            Log.e(LOG_TAG, "Error in operating files: " + e);
        }
    }

}