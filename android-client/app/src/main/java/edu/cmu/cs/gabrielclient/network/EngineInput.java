package edu.cmu.cs.gabrielclient.network;

import android.hardware.Camera;

public class EngineInput {
    byte[] frame;
    Camera.Parameters parameters;

    public EngineInput(byte[] frame, Camera.Parameters parameters) {
        this.frame = frame;
        this.parameters = parameters;
    }
}