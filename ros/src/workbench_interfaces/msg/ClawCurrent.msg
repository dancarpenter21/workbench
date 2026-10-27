std_msgs/Header header
string domain
bool valid
bool scale_verified
float64 bus_voltage_v
float64 shunt_voltage_mv
# NaN when scale_verified is false. Never infer zero current from missing data.
float64 current_a
string verification_evidence
bool fault_latched
string fault
