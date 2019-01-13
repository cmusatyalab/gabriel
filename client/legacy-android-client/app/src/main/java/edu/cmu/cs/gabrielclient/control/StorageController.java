package edu.cmu.cs.gabrielclient.control;

import edu.cmu.cs.gabrielclient.Const;
import edu.cmu.cs.gabrielclient.util.LifeCycleIF;

public class StorageController implements LifeCycleIF {
    @Override
    public void onResume() {
        Const.ROOT_DIR.mkdirs();
        Const.EXP_DIR.mkdirs();
        if (Const.SAVE_FRAME_SEQUENCE) {
            Const.SAVE_FRAME_SEQUENCE_DIR.mkdirs();
        }
    }

    @Override
    public void onPause() {

    }

    @Override
    public void onDestroy() {

    }
}
