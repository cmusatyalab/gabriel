package edu.cmu.cs.gabrielclient;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;
import android.widget.VideoView;

import edu.cmu.cs.gabrielclient.control.CameraPreviewController;
import edu.cmu.cs.gabrielclient.control.InstructionViewController;
import edu.cmu.cs.gabrielclient.control.ResourceMonitorController;
import edu.cmu.cs.gabrielclient.control.ServerController;
import edu.cmu.cs.gabrielclient.control.StorageController;
import edu.cmu.cs.gabrielclient.control.TokenController;
import edu.cmu.cs.gabrielclient.network.ConnectionConfig;
import edu.cmu.cs.gabrielclient.network.LogicalTime;
import edu.cmu.cs.gabrielclient.network.NetworkProtocol;
import edu.cmu.cs.gabrielclient.stream.ResultStream;
import edu.cmu.cs.gabrielclient.stream.VideoStream;

public class GabrielClientActivity extends Activity {

    private static final String LOG_TAG = GabrielClientActivity.class.getSimpleName();
    // general set up
    private LifeCycleManager lifeCycleManager;
    // activity views
    private CameraPreview cameraPreview = null;
    private ImageView imgView = null;
    private VideoView videoView = null;
    private TextView subtitleView = null;
    // controllers: controlThread, tokenController, and instruction view controller
    private ServerController serverController = null;
    private InstructionViewController ivController = null;
    private TokenController tokenController = null;
    // handling results from server
    private ResultStream resultStream = null;
    // measurements
    private LogicalTime logicalTime = null;
    // Handles messages passed from streaming threads and result receiving threads.
    private Handler uiHandler = new UIThreadHandler();


    @Override
    protected void onCreate(Bundle savedInstanceState) {
        Log.v(LOG_TAG, "++onCreate");
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED +
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON +
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        initViews();
        lifeCycleManager = LifeCycleManager.getInstance();
        // initialize controllers
        // TODO (junjuew): add background ping thread
        CameraPreviewController cpc = new CameraPreviewController(cameraPreview);
        lifeCycleManager.add(cpc);
        ivController = new InstructionViewController(this.getApplicationContext(), imgView,
                videoView,
                subtitleView);
        lifeCycleManager.add(ivController);
        tokenController = new TokenController(Const.TOKEN_SIZE, null);
        lifeCycleManager.add(tokenController);
        serverController = new ServerController(new ConnectionConfig(Const.SERVER_IP, Const
                .CONTROL_PORT,
                tokenController, uiHandler, null));
        lifeCycleManager.add(serverController);
        StorageController sc = new StorageController();
        lifeCycleManager.add(sc);
        if (Const.MONITOR_RESOURCE) {
            ResourceMonitorController rmc = new ResourceMonitorController(this
                    .getApplicationContext());
            lifeCycleManager.add(rmc);
        }
        // initialize data streams
        // TODO(junjuew): add audio and sensor data streams
        resultStream = new ResultStream(new ConnectionConfig(Const.SERVER_IP, Const
                .RESULT_RECEIVING_PORT,
                tokenController, uiHandler, null));
        lifeCycleManager.add(resultStream);
        if (Const.SENSOR_VIDEO) {
            VideoStream vs = new VideoStream(new ConnectionConfig(Const.SERVER_IP, Const
                    .VIDEO_STREAM_PORT,
                    tokenController, uiHandler, null));
            lifeCycleManager.add(vs);
        }
    }

    @Override
    protected void onResume() {
        Log.v(LOG_TAG, "++onResume");
        super.onResume();
        lifeCycleManager.onResume();
    }

    @Override
    protected void onPause() {
        Log.v(LOG_TAG, "++onPause");
        lifeCycleManager.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        Log.v(LOG_TAG, "++onDestroy");
        lifeCycleManager.onDestroy();
        super.onDestroy();
    }

    private void initViews() {
        imgView = findViewById(R.id.guidance_image);
        videoView = findViewById(R.id.guidance_video);
        subtitleView = findViewById(R.id.subtitleText);
        cameraPreview = findViewById(R.id.camera_preview);
        if (Const.SHOW_SUBTITLES) {
            findViewById(R.id.subtitleText).setVisibility(View.VISIBLE);
        }
    }

    private void showAlert(Message msg) {
        Toast.makeText(this, (String) msg.obj,
                Toast.LENGTH_SHORT).show();
    }

    private class UIThreadHandler extends Handler {
        @Override
        public void handleMessage(Message msg) {
            switch (msg.what) {
                case NetworkProtocol.NETWORK_CONNECT_FAILED:
                    showAlert(msg);
                    break;
                case NetworkProtocol.NETWORK_RET_MESSAGE:
                    String inst = resultStream.parseReturnMsg((String) msg.obj);
                    if (inst != null) {
                        ivController.parseAndSetInstruction(inst);
                    }
                    break;
                case NetworkProtocol.NETWORK_RET_CONFIG:
                    // TODO(junjuew): add network re-configure functionality
                    Log.e(LOG_TAG, "Network Reconfigure Functionality is not implemented");
                    System.exit(-1);
                    break;
                default:
                    Log.e(LOG_TAG, "unrecognized message type to UI: " + msg.what);
            }
        }
    }
}
