package edu.cmu.cs.gabriel.client.consumer;

import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.io.PrintStream;
import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.observer.IntervalMeasurement;

public class CsvMeasurementConsumer implements Consumer<IntervalMeasurement> {
    private static final String TAG = "CsvMeasurementConsumer";
    private static final String FILE_PREFIX = "gabriel";
    private static final String FILE_SUFFIX = ".txt";  // Stock Android can open .txt , but not .csv
    private static final String CSV_HEADING =
            "source name,interval rtt,overall rtt,interval fps,overall fps";

    private PrintStream printStream;
    private boolean createSucceeded;

    public CsvMeasurementConsumer(String directoryName) {
        File resultDirectory = new File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS),
                directoryName);
        if (!resultDirectory.exists()){
            if (!resultDirectory.mkdirs()) {
                Log.e(TAG, "Failed to create directory for results!");
                return;
            }
        }

        try {
            File resultsFile = File.createTempFile(FILE_PREFIX, FILE_SUFFIX, resultDirectory);
            this.printStream = new PrintStream(resultsFile);
            this.printStream.println(CSV_HEADING);
            this.createSucceeded = true;
        } catch (IOException e) {
            Log.e(TAG, "Error creating CSV file", e);
            this.createSucceeded = false;
        }
    }

    public boolean getCreateSucceeded() {
        return createSucceeded;
    }

    @Override
    public void accept(IntervalMeasurement intervalMeasurement) {
        if (!createSucceeded) {
            Log.e(TAG, "Results file was not created");
        }

        this.printStream.println(
                intervalMeasurement.getSourceName() + "," + intervalMeasurement.getIntervalRtt()
                        + "," + intervalMeasurement.getOverallRtt() + ","
                        + intervalMeasurement.getIntervalFps() + ","
                        + intervalMeasurement.getOverallFps());
    }

    /**
     * Save CSV file to the device.
     * @return True if save succeeded
     */
    public boolean saveFile() {
        if (!createSucceeded) {
            return false;
        }

        this.printStream.flush();
        this.printStream.close();
        return true;
    }
}
