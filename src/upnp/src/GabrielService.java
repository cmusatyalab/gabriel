
import org.teleal.cling.binding.annotations.*;

@UpnpService(
        serviceId = @UpnpServiceId(GabrielService.SERVICE_ID),
        serviceType = @UpnpServiceType(value = "port", version = 8021)
)

public class GabrielService {
	
	public static final String SERVICE_ID = "Cloudlet-Gabriel"; 

    @UpnpStateVariable(defaultValue = "0", sendEvents = false)
    private boolean target = false;

    @UpnpStateVariable(defaultValue = "0")
    private boolean status = false;

    @UpnpAction
    public void setTarget(@UpnpInputArgument(name = "NewTargetValue")
                          boolean newTargetValue) {
        target = newTargetValue;
        status = newTargetValue;
        System.out.println("Switch is: " + status);
    }

    @UpnpAction(out = @UpnpOutputArgument(name = "RetTargetValue"))
    public boolean getTarget() {
        return target;
    }

    @UpnpAction(out = @UpnpOutputArgument(name = "ResultStatus"))
    public boolean getStatus() {
        return status;
    }

}