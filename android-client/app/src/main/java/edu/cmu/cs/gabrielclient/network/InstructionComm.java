package edu.cmu.cs.gabrielclient.network;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

import com.google.protobuf.ByteString;
import com.google.protobuf.InvalidProtocolBufferException;

import edu.cmu.cs.gabriel.client.comm.ServerComm;
import edu.cmu.cs.gabriel.client.comm.ServerCommCore;
import edu.cmu.cs.gabriel.client.function.Consumer;
import edu.cmu.cs.gabriel.protocol.Protos;
import edu.cmu.cs.gabrielclient.Const;
import edu.cmu.cs.gabrielclient.R;
import edu.cmu.cs.gabriel.instruction.Protos.EngineFields;

public class InstructionComm {
    private static String TAG = "InstructionComm";

    ServerCommCore serverCommCore;
    Consumer<Protos.ResultWrapper> consumer;
    Runnable onDisconnect;
    private boolean shownError;
    private EngineFields engineFields;

    public InstructionComm(
            String serverURL, final Activity activity, final Handler returnMsgHandler) {
        engineFields = EngineFields.newBuilder().build();

        this.consumer = new Consumer<Protos.ResultWrapper>() {
            @Override
            public void accept(Protos.ResultWrapper resultWrapper) {
                try {
                    EngineFields newEngineFields = EngineFields.parseFrom(
                            resultWrapper.getEngineFields().getValue());
                    if (Const.DEDUPLICATE_RESPONSE_BY_ENGINE_UPDATE_COUNT &&
                            newEngineFields.getUpdateCount() <= engineFields.getUpdateCount()) {
                        // There was no update or there was an update based on a stale frame
                        return;
                    }
                    engineFields = newEngineFields;

                    for (int i = 0; i < resultWrapper.getResultsCount(); i++) {
                        Protos.ResultWrapper.Result result = resultWrapper.getResults(i);
                        if (!result.getEngineName().equals(Const.ENGINE_NAME)) {
                            Log.e(TAG, "Got result from engine " + result.getEngineName());
                        }

                        if (result.getPayloadType() == Protos.PayloadType.IMAGE) {
                            ByteString dataString = result.getPayload();

                            Bitmap imageFeedback = BitmapFactory.decodeByteArray(
                                    dataString.toByteArray(), 0, dataString.size());
                            Message msg = Message.obtain();
                            msg.what = NetworkProtocol.NETWORK_RET_IMAGE;
                            msg.obj = imageFeedback;
                            returnMsgHandler.sendMessage(msg);
                        } else if (result.getPayloadType() == Protos.PayloadType.TEXT) {
                            ByteString dataString = result.getPayload();
                            String speechFeedback = dataString.toStringUtf8();
                            Message msg = Message.obtain();
                            msg.what = NetworkProtocol.NETWORK_RET_SPEECH;
                            msg.obj = speechFeedback;
                            returnMsgHandler.sendMessage(msg);
                        }
                    }
                } catch (InvalidProtocolBufferException e) {
                    Log.e(TAG, "Error getting engine fields", e);
                }
            }
        };

        this.onDisconnect = new Runnable() {
            @Override
            public void run() {
                Log.i(TAG, "Disconnected");
                String message = InstructionComm.this.serverCommCore.isRunning()
                        ? activity.getResources().getString(R.string.server_disconnected)
                        : activity.getResources().getString(R.string.could_not_connect);

                if (InstructionComm.this.shownError) {
                    return;
                }

                InstructionComm.this.shownError = true;

                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_FAILED;
                Bundle data = new Bundle();
                data.putString("message", message);
                msg.setData(data);
                returnMsgHandler.sendMessage(msg);
            }
        };

        this.shownError = false;
        this.serverCommCore = new ServerComm(this.consumer, this.onDisconnect, serverURL,
                activity.getApplication());
    }

    public EngineFields getEngineFields() {
        return this.engineFields;
    }

    public void sendSupplier(FrameSupplier frameSupplier) {
        this.serverCommCore.sendSupplier(frameSupplier);
    }

    public void stop() {
        this.serverCommCore.stop();
    }
}
