package edu.cmu.cs.gabriel.network;

public class NetworkProtocol {
    // TODO: give better names to these constants

    public static final int NETWORK_RET_FAILED = 1;
    public static final int NETWORK_RET_SPEECH = 2;
    public static final int NETWORK_RET_CONFIG = 3;
    public static final int NETWORK_RET_TOKEN = 4;
    public static final int NETWORK_RET_IMAGE = 5;
    public static final int NETWORK_RET_VIDEO = 6;
    public static final int NETWORK_RET_ANIMATION = 7;
    public static final int NETWORK_RET_MESSAGE = 8;
    public static final int NETWORK_RET_DONE = 9;
    public static final int NETWORK_RET_SYNC = 10;

    public static final String HEADER_MESSAGE_CONTROL = "control";
    public static final String HEADER_MESSAGE_RESULT = "result";
    public static final String HEADER_MESSAGE_INJECT_TOKEN = "token_inject";
    public static final String HEADER_MESSAGE_FRAME_ID = "frame_id";
    public static final String HEADER_MESSAGE_ENGINE_ID = "engine_id";

    public static final String SERVER_CONTROL_FPS = "fps";
    public static final String SERVER_CONTROL_IMG_WIDTH = "img_width";
    public static final String SERVER_CONTROL_IMG_HEIGHT = "img_height";
}
