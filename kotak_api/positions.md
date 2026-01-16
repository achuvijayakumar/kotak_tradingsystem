Positions
Gets positions

client.positions()
Example
from neo_api_client import NeoAPI


#First initialize session and generate session token
client = NeoAPI(environment='prod', access_token=None, neo_fin_key=None)
client.totp_login(mobilenumber="", ucc="", totp='')
client.totp_validate(mpin="")

try:
    client.positions()
except Exception as e:
    print("Exception when calling PositionsApi->positions: %s\n" % e)
Return type
object

Sample response
{
    "stat": "ok",
    "stCode": 200,
    "data": [
        {
            "actId": "",
            "algCat": "NA",
            "algId": "NA",
            "avgPrc": "9.39",
            "boeSec": 1737536296,
            "brdLtQty": 1,
            "brkClnt": "NA",
            "cstFrm": "C",
            "exOrdId": "1100000059569867",
            "exp": "-",
            "expDt": "NA",
            "exSeg": "nse_cm",
            "exTm": "22-Jan-2025 14:28:01",
            "fldQty": 1,
            "flDt": "22-Jan-2025",
            "flId": "207983744",
            "flLeg": 1,
            "flTm": "14:28:16",
            "minQty": 0,
            "nOrdNo": "250122000612876",
            "nReqId": "1",
            "optTp": "- ",
            "ordDur": "NA",
            "ordGenTp": "--",
            "prcTp": "L",
            "prod": "NRML",
            "rmk": "--",
            "rptTp": "fill",
            "series": "EQ",
            "stkPrc": "0.00",
            "sym": "IDEA",
            "trdSym": "IDEA-EQ",
            "trnsTp": "B",
            "usrId": "AVRPC7535J",
            "genDen": "1",
            "genNum": "1",
            "hsUpTm": "2025/01/22 14:28:16",
            "GuiOrdId": "",
            "locId": "111111111111100",
            "lotSz": "1",
            "multiplier": "1",
            "ordSrc": "NA",
            "prcNum": "1",
            "prcDen": "1",
            "strategyCode": "",
            "precision": "2",
            "tok": "",
            "updRecvTm": 1737536296355319176,
            "uSec": "1737536296",
            "posFlg": "",
            "prc": "",
            "qty": 0,
            "tm": "",
            "it": "EQ"
        }
    ]
}
Positions Calculations
Quantity Fields
Total Buy Qty = (cfBuyQty + flBuyQty)
Total Sell qty = (cfSellQty + flSellQty)
Carry Fwd Qty = (cfBuyQty - cfSellQty)
Net qty = Total Buy Qty - Total Sell qty
For FnO Scrips, divide all the parameters from Positions API response(cfBuyQty, flBuyQty, cfSellQty, flSellQty) by lotSz
Amount Fields
Total Buy Amt = (cfBuyAmt + buyAmt)
Total Sell Amt = (cfSellAmt + sellAmt)
Avg Price Fields
Buy Avg Price = Total Buy Amt/(Total Buy Qty * multiplier * (genNum/genDen) * (prcNum/ prcDen))

Sell Avg Price = Total Sell Amt/(Total Sell qty * multiplier * (genNum/ genDen) * (prcNum/ prcDen))

Avg Price
a. If Total Buy Qty > Total Sell qty, then Buy Avg Price
b. If Total Buy Qty < Total Sell qty, then Sell Avg Price
c. If Total Buy Qty = Total Sell qty, then 0
You need to calculate the average price to a specific number of decimal places that is decided by precision field.

Profit N Loss
PnL = (Total Sell Amt - Total Buy Amt) + (Net qty * LTP * multiplier * (genNum/genDen) * (prcNum/prcDen) )

HTTP request headers
Accept: application/json
HTTP response details
Status Code	Description
200	Gets the Positoin data for a client account
400	Invalid or missing input parameters
403	Invalid session, please re-login to continue
429	Too many requests to the API
500	Unexpected error
502	Not able to communicate with OMS
503	Trade API service is unavailable
504	Gateway timeout, trade API is unreachable