package edu.cmu.cs.gabriel.camera;

import android.graphics.Bitmap;

public interface BitmapAnalyzer {
    boolean analyze(Bitmap bitmap, long recordReplayTimeOffset, boolean done);
}
