package edu.cmu.cs.gabriel.network;

import android.util.Log;
import edu.cmu.cs.gabriel.Const;

import java.io.BufferedReader;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.InetAddress;

/**
 * Created by junjuew on 5/14/16.
 */
public class PingThread extends Thread {
    private static final String LOG_TAG = "Ping Thread";
    private volatile boolean running = true;

    private String serverIP = null;
    private float ping_interval = -1;
    Process p;

    public PingThread(String serverIP, int interval){
    	this.serverIP = serverIP;
        ping_interval = (float) interval / 1000;
        Log.i(LOG_TAG, "ping interval: "+ping_interval);
    }


    public void kill(){
        if (p != null){
            Log.i(LOG_TAG, " kill process");
            p.destroy();
        }
    }

    public void run() {
        if (ping_interval == 0){

        } else {
            Log.i(LOG_TAG, "ping thread running");
            System.out.println("executeCommand");
            Runtime runtime = Runtime.getRuntime();
//            String cmd="su -c ";
//            cmd+="'" + ping_cmd + "'";
//            String[] deviceCommands = {"su", ping_cmd};

            String ping_cmd="/system/bin/ping " +"-i "
                    + String.valueOf(ping_interval) +" " + serverIP;
            Log.i(LOG_TAG, "ping cmd: " + ping_cmd);
            try
            {
                p = runtime.exec("echo");
                DataOutputStream os = new DataOutputStream(p.getOutputStream());
                BufferedReader reader = new BufferedReader(new InputStreamReader(
                        p.getInputStream()));
                Thread.sleep(10);
                os.writeBytes(ping_cmd + "\n");
                os.flush();

                // wait until the command is finished
//            cmd = "echo -n 0\n";
//            os.write(cmd.getBytes("ASCII"));
//            os.flush();
//            reader.read();

                while (true) {
                	Log.v(LOG_TAG, "aaa");
                    Thread.sleep(5);
                    Log.v(LOG_TAG, "bbb");
                    String s = null;
                    int idx=0;
                    while ((s = reader.readLine()) != null) {
                        if (idx % 500 != 0){
                            Log.v(LOG_TAG, s);
                        }
                        idx++;
                    }
                }
            }
            catch (InterruptedException ignore)
            {
                ignore.printStackTrace();
                System.out.println(" Exception:"+ignore);
            }
            catch (IOException e)
            {
                e.printStackTrace();
                System.out.println(" Exception:"+e);
            }
        }
        Log.i(LOG_TAG, "ping thread finished");
    }


}
