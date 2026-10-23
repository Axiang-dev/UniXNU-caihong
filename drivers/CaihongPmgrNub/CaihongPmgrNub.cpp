// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Stand-in for Apple's ApplePMGRNub.
//
// AMFI's ConfigurationSettings looks up a service named "ApplePMGRNub" and
// panics with "AMFI: No PMGR?" when it finds none. Apple's own ApplePMGR driver
// only matches AppleARMIODevice providers, which ArmDTPlatform never publishes
// (it publishes IOPlatformDevice), and the PMGR hardware it would drive does not
// exist on SM8650.
//
// The class name is twelve characters so the payload step can rename AMFI's
// lookup string in place. It publishes the properties AMFI reads and nothing
// else: no hardware access, no PMGR tables.
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>

class CaihongPMGRN : public IOService {
  OSDeclareDefaultStructors (CaihongPMGRN)

public:
  bool start (IOService *provider) override;
};

OSDefineMetaClassAndStructors (CaihongPMGRN, IOService);

bool
CaihongPMGRN::start (IOService *provider)
{
  if (!IOService::start (provider)) {
    return false;
  }

  // Values AMFI reads after the lookup. The PMGR always-on domain is reported as
  // latched so the check passes; nothing here touches hardware.
  setProperty ("pmgr-aon", true);
  setProperty ("pmgr-aon-latched", true);
  setProperty ("pmgr-provider", "caihong");

  registerService ();
  IOLog ("CaihongPMGRN: PMGR stand-in registered\n");
  return true;
}
