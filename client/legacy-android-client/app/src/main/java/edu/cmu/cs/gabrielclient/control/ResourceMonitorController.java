package edu.cmu.cs.gabrielclient.control;

import android.content.Context;
import android.content.Intent;
import android.util.Log;

import edu.cmu.cs.gabrielclient.util.LifeCycleIF;
import edu.cmu.cs.gabrielclient.util.ResourceMonitoringService;

/*
 * Resource monitoring of the mobile device
 * Checks battery and CPU usage, as well as device temperature
 */
public class ResourceMonitorController implements LifeCycleIF {
    private static final String LOG_TAG = ResourceMonitorController.class.getSimpleName();
    private Intent resourceMonitoringIntent;
    private Context context;

    public ResourceMonitorController(Context context){
        this.context = context;
    }

    @Override
    public void onResume() {
        Log.i(LOG_TAG, "Starting Battery Recording Service");
        resourceMonitoringIntent = new Intent(this.context, ResourceMonitoringService.class);
        this.context.startService(resourceMonitoringIntent);
    }

    @Override
    public void onPause() {
        Log.i(LOG_TAG, "Stopping Battery Recording Service");
        if (resourceMonitoringIntent != null) {
            this.context.stopService(resourceMonitoringIntent);
            resourceMonitoringIntent = null;
        }
    }

    @Override
    public void onDestroy() {

    }
}
