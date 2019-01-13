package edu.cmu.cs.gabrielclient.control;

import edu.cmu.cs.gabrielclient.CameraPreview;
import edu.cmu.cs.gabrielclient.stream.VideoStream;
import edu.cmu.cs.gabrielclient.util.LifeCycleIF;

public class CameraPreviewController implements LifeCycleIF {
    private CameraPreview preview;

    public CameraPreviewController(CameraPreview preview){
        this.preview = preview;
    }

    @Override
    public void onResume() {
        // Camera preview callbacks needs to be registered onResume
        preview.start(CameraPreview.CameraConfiguration.getInstance(), VideoStream.previewCallback);
    }

    @Override
    public void onPause() {
        preview.close();
    }

    @Override
    public void onDestroy() {

    }
}
