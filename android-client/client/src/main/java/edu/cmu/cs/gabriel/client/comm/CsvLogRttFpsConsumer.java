package edu.cmu.cs.gabriel.client.comm;

import android.content.Context;
import android.os.Environment;
import android.util.Log;
import android.widget.Toast;

import java.io.File;
import java.io.IOException;
import java.io.PrintStream;

import edu.cmu.cs.gabriel.client.function.Consumer;

public class CsvLogRttFpsConsumer implements Consumer<RttFps> {
    private static final String TAG = "LogRttFpsConsumer";
    private static final String FILE_PREFIX = "gabriel";
    private static final String FILE_SUFFIX = ".txt";  // Stock Android can open .txt , but not .csv
    private static final String CSV_HEADING = "rtt,fps";
    private static final CharSequence TOAST_ERROR_TEXT = "Could not create CSV file";

    private PrintStream printStream;

    public CsvLogRttFpsConsumer(String directoryName, Context context) {
        File resultDirectory = new File(Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOCUMENTS), directoryName);
        if (!resultDirectory.exists()){
            if (!resultDirectory.mkdirs()) {
                Log.e(TAG, "Failed to create directory for results!");
                return;
            }
        }


        try {
            File resultsFile = File.createTempFile(FILE_PREFIX, FILE_SUFFIX, resultDirectory);
            this.printStream = new PrintStream(resultsFile);
        } catch (IOException e) {
            Log.e(TAG, "Error creating CSV file", e);
            Toast.makeText(context, TOAST_ERROR_TEXT, Toast.LENGTH_LONG).show();
        }
        this.printStream.println(CSV_HEADING);
    }

    @Override
    public void accept(RttFps rttFps) {
        if (this.printStream == null) {
            Log.e(TAG, "Results file was not created");
        }

        this.printStream.println(rttFps.getRtt() + "," + rttFps.getFps());
    }

    /**
     * Save CSV file to the device.
     * @return True if save succeeded
     */
    public boolean saveFile() {
        if (this.printStream == null) {
            return false;
        }

        this.printStream.flush();
        this.printStream.close();
        return true;
    }
}
