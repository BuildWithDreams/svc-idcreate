from slickrpc import Proxy
import base64
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# refactor/2025-1 consider error handling strategies
# from retry import retry

# @retry(Exception, tries=3, delay=1, backoff=2)
# def list_currencies_with_retry(self, daemon: str, params: Dict[str, Any] = None) -> List[Dict]:
#     return self.daemonrpc[daemon].list_currencies(params or {})

class NodeRpc:
    def __init__(self, rpc_user, rpc_password, rpc_port, node_ip):
        self.rpc_user = rpc_user
        self._rpc_password = rpc_password
        self.rpc_port = rpc_port
        self.node_ip = node_ip
        self.rpc_connection = self.rpc_connect(rpc_user, rpc_password, rpc_port, node_ip)

    def rpc_connect(self, rpc_user, rpc_password, rpc_port, node_ip):
        # SECURITY FIX: Don't log the password!
        masked_pass = rpc_password[:2] + "****" if rpc_password else "****"
        logger.info(f"Connecting to http://{rpc_user}:{masked_pass}@{node_ip}:{rpc_port}")
        
        try:
            rpc_connection = Proxy(f"http://{rpc_user}:{rpc_password}@{node_ip}:{rpc_port}")
            logger.info(
                "RPC proxy object created host=%s port=%s user=%s proxy_type=%s",
                node_ip,
                rpc_port,
                rpc_user,
                type(rpc_connection).__name__,
            )
        except Exception as e:
            # Log the full error but re-raise
            logger.exception(
                "RPC proxy creation failed host=%s port=%s user=%s error_type=%s",
                node_ip,
                rpc_port,
                rpc_user,
                type(e).__name__,
            )
            raise Exception(f"Connection error: {e}")
        return rpc_connection


    def import_priv_key(self, priv_key):
        try:
            self.rpc_connection.importprivkey(priv_key)
        except Exception as e:
            raise Exception(f"Error importing private key: {e}")


    def get_balance(self, addr):
        try:
            balance = self.rpc_connection.getbalance()
        except Exception as e:
            raise Exception(f"Error getting balance: {e}")
        return balance


    def get_utxos(self, addr):
        try:
            utxos = self.rpc_connection.listunspent(1, 9999999, [addr])
        except Exception as e:
            raise Exception(f"Error getting UTXOs: {e}")
        return utxos


    def get_transaction(self, txid):
        try:
            transaction = self.rpc_connection.gettransaction(txid)
        except Exception as e:
            raise Exception(f"Error getting transaction: {e}")
        return transaction


    def get_network_status(self):
        try:
            info = self.rpc_connection.getinfo()
        except Exception as e:
            raise Exception(f"Error getting network status: {e}")
        return info


    def list_currencies(self, from_system_query_object=None):
        try:
            if from_system_query_object is None:
                currencies = self.rpc_connection.listcurrencies()
            else:
                currencies = self.rpc_connection.listcurrencies(from_system_query_object)
        # refactor/2025-1 consider more precise exception handling & logger
        # except (RPCError, ConnectionError) as e:
        #     logger.error(f"RPC failure for daemon {daemon}: {str(e)}")
        #     raise RuntimeError(f"RPC failure for daemon {daemon}: {str(e)}")
        except Exception as e:
            raise Exception(f"Error listing currencies: {e}")
        return currencies


    def broadcast(self, signedtx):
        # print(f"Broadcasting {signedtx}")
        try:
            tx_id = self.rpc_connection.sendrawtransaction(signedtx)
        except Exception as e:
            raise Exception(f"Error broadcasting transaction: {e}")
        return tx_id


    def get_info(self):
        logger.info("Calling getinfo host=%s port=%s user=%s", self.node_ip, self.rpc_port, self.rpc_user)
        try:
            info = self.rpc_connection.getinfo()
        except Exception as e:
            probe = self._probe_rpc_http("getinfo")
            logger.exception(
                "getinfo failed host=%s port=%s user=%s error_type=%s probe=%s",
                self.node_ip,
                self.rpc_port,
                self.rpc_user,
                type(e).__name__,
                probe,
            )
            raise Exception(f"Error getting node info: {e}; probe={probe}")
        return info

    def _probe_rpc_http(self, method: str) -> dict:
        """Best-effort raw RPC probe to explain decode/connect/auth failures."""
        url = f"http://{self.node_ip}:{self.rpc_port}"
        payload = json.dumps({"jsonrpc": "1.0", "id": "health-probe", "method": method, "params": []}).encode("utf-8")
        auth = base64.b64encode(f"{self.rpc_user}:{self._rpc_password}".encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return {
                    "transport": "http",
                    "status": resp.getcode(),
                    "content_type": resp.headers.get("Content-Type"),
                    "body_preview": body[:200],
                }
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace") if hasattr(err, "read") else str(err)
            return {
                "transport": "http",
                "status": err.code,
                "content_type": getattr(err, "headers", {}).get("Content-Type") if getattr(err, "headers", None) else None,
                "body_preview": body[:200],
            }
        except urllib.error.URLError as err:
            return {
                "transport": "http",
                "error_type": "URLError",
                "reason": str(err.reason),
            }
        except Exception as err:
            return {
                "transport": "http",
                "error_type": type(err).__name__,
                "reason": str(err),
            }


    def get_currency_state(self, currency_name, height_range=''):
        try:
            currency_state_result = self.rpc_connection.getcurrencystate(currency_name, height_range)
        except Exception as e:
            raise Exception(f"Error retrieving currency state: {e}")
        return currency_state_result

    def get_pending_transfers(self, currency_name):
        try:
            pending_transfers_result = self.rpc_connection.getpendingtransfers(currency_name)
        except Exception as e:
            raise Exception(f"Error retrieving pending transfers in {currency_name}: {e}")
        return pending_transfers_result
    
    
    def get_imports(self, currency_name, start_height, end_height=''):
        try:
            get_imports_result = self.rpc_connection.getimports(currency_name, start_height)
        except Exception as e:
            raise Exception(f"Error retrieving get imports in {currency_name} start height {start_height}: {e}")
        return get_imports_result


    def send_currency(self, from_address, params):
        # print(from_address)
        # print(params)
        try:
            send_currency_result = self.rpc_connection.sendcurrency(from_address, params)
        except Exception as e:
            raise Exception(f"Error sending currency: {e}")
        return send_currency_result


    def send_currency_simple_to_identity(self, from_address, currency, identity, amount):
        params = [{"currency": currency, "address": identity, "amount": amount}]
        print(params)
        return self.send_currency(from_address, params)


    def send_currency_via(self, currency, convertto, via, amount, address):
        params = [{"currency": currency, "convertto": convertto, "via": via, "amount": amount, "address": address}]
        print(f"send currency params: {params}")
        try:
            send_currency_result = self.send_currency(address, params)
        except Exception as e:
            raise Exception(f"Error sending currency: {e}")
        return send_currency_result


    def get_wallet_info(self):
        try:
            result = self.rpc_connection.getwalletinfo()
        except Exception as e:
            raise Exception(f"Error retrieving wallet info: {e}")
        return result


    def z_get_operation_status(self, opid):
        opids = [opid]
        # print(f"z {opids}")
        try:
            result = self.rpc_connection.z_getoperationstatus(opids)
            # print(result)
        except Exception as e:
            raise Exception(f"Error retrieving operation status: {e}")
        return result


    def register_name_commitment(self, name, control_address, referral_id, parent="VRSC", source_of_funds="*"):
        self.get_info()
        print(f"{name}, {control_address}, {referral_id}, {parent}, {source_of_funds}")
        try:
            result = self.rpc_connection.registernamecommitment(name, control_address, referral_id, parent, source_of_funds)
        except Exception as e:
            raise Exception(f"Error with registering name commitment: {e}")
        return result


    def register_identity(self, json_namecommitment_response, json_identity, source_of_funds, fee_offer=80):
        # json_identity is an object added to the namecommitment result object as identity attribute
        json_namecommitment_response["identity"] = json_identity
        print(json_namecommitment_response)
        try:
            result = self.rpc_connection.registeridentity(json_namecommitment_response, False, fee_offer, source_of_funds)
        except Exception as e:
            raise Exception(f"Error with registering identity: {e}")
        return result


    def update_identity(self, update_params):
        try:
            result = self.rpc_connection.updateidentity(update_params)
        except Exception as e:
            raise Exception(f"Error updating identity: {e}")
        return result

    def get_vdxf_id(self, key_name):
        try:
            result = self.rpc_connection.getvdxfid(key_name)
        except Exception as e:
            raise Exception(f"Error retrieving VDXF id for '{key_name}': {e}")
        return result

    def get_identity_content(
        self,
        identity_name_or_id,
        height_start=0,
        height_end=0,
        tx_proofs=False,
        tx_proof_height=0,
        vdxf_key=None,
        keep_deleted=False,
    ):
        args = [identity_name_or_id, height_start, height_end, tx_proofs, tx_proof_height]

        # Include optional args only when requested to keep compatibility with daemon defaults.
        if vdxf_key is not None or keep_deleted:
            args.append(vdxf_key if vdxf_key is not None else "")
        if keep_deleted:
            args.append(keep_deleted)

        try:
            result = self.rpc_connection.getidentitycontent(*args)
        except Exception as e:
            raise Exception(f"Error retrieving identity content for '{identity_name_or_id}': {e}")
        return result

    def decrypt_data(self, decrypt_payload):
        try:
            result = self.rpc_connection.decryptdata(decrypt_payload)
        except Exception as e:
            raise Exception(f"Error decrypting data: {e}")
        return result

    def build_contentmultimap_data_wrapper(
        self,
        vdxf_key,
        identity_address,
        filename,
        label=None,
        mimetype="application/octet-stream",
        create_mmr=True,
    ):
        data_object = {
            "address": identity_address,
            "filename": filename,
            "createmmr": create_mmr,
            "mimetype": mimetype,
        }
        if label:
            data_object["label"] = label

        return {
            vdxf_key: [
                {
                    "data": data_object,
                }
            ]
        }
#"params": [
# {
#   "name":"dude",
#   "contentmultimap":
#     { "iCtawpxUiCc2sEupt7Z4u8SDAncGZpgSKm": [
#        {"i4GC1YGEVD21afWudGoFJVdnfjJ5XWnCQv":{
#           "version":1,
#           "flags":0,
#           "label":"dude.vrsc::nft.simple.name",
#           "mimetype":"text/plain",
#           "objectdata":{
#              "message":"dudes nft"
#            }
#         }
#      },
#      {
#         "data": {
#            "createmmr":true,
#            "mmrdata":[
#              {"filename":"/Users/dude/Desktop/dude.png","mimetype":"picture/PNG"}
#            ]
#          }
#       }
#     ]
#    }
#   }
#  ]}

    def get_raw_transaction(self, txid, verbose=1):
        try:
            result = self.rpc_connection.getrawtransaction(txid, verbose)
        except Exception as e:
            raise Exception(f"Error with get raw transaction: {e}")
        return result


    def define_currency(self, params):
        try:
            result = self.rpc_connection.definecurrency(params)
        except Exception as e:
            raise Exception(f"Error with define currency: {e}")
        print(json.dumps(result))
        return self.broadcast(result["hex"])


    def define_simple_token_currency(self, options, name, id_registration_fees, pre_allocations, proof_protocol):
        params = {"options": options, "name": name, "idregistrationfees": id_registration_fees, "preallocations": pre_allocations, "proofprotocol": proof_protocol}
        try:
            result = self.rpc_connection.definecurrency(params)
        except Exception as e:
            raise Exception(f"Error with define simple token currency: {e}")
        print(json.dumps(result))
        return self.broadcast(result["hex"])

    def define_define_id_control_token(self, options, name, pre_allocations):
        params = {"options": options, "name": name, "preallocations": pre_allocations, "maxpreconversion": [0]}
        # print(params)
        try:
            result = self.rpc_connection.definecurrency(params)
        except Exception as e:
            raise Exception(f"Error with define id control token currency: {e}")
        print(json.dumps(result))
        return self.broadcast(result["hex"])



    def get_currency_balance(self, from_address):
        try:
            result = self.rpc_connection.getcurrencybalance(from_address)
        except Exception as e:
            raise Exception(f"Error with get currency balance: {e}")
        return result


    def get_currency(self, currency_name_or_id):
        try:
            result = self.rpc_connection.getcurrency(currency_name_or_id)
        except Exception as e:
            raise Exception(f"Error with get currency: {e}")
        return result


    def get_identity(self, identity_name_or_id):
        try:
            result = self.rpc_connection.getidentity(identity_name_or_id)
        except Exception as e:
            raise Exception(f"Error with get identity: {e}")
        return result


    def get_address_balance(self, raddress):
        params = {"addresses": [raddress], "friendlynames": 1}
        try:
            result = self.rpc_connection.getaddressbalance(params)
        except Exception as e:
            raise Exception(f"Error with get address balance: {e}")
        return result
    
    def get_currency_converters(self, currency1, currency2):
        params = {"fromcurrency": currency1, "convertto": currency2}
        # print(json.dumps(params))
        try:
            result = self.rpc_connection.getcurrencyconverters(params)
        except AttributeError as e:
            raise AttributeError(f"Error type with get currency converters: {e}")
        return result
    
def estimate_conversion(self, currency1, currency2, amount, via=None):
        """
        Calculates the estimated output for a conversion.
        """
        params = {"currency": currency1, "convertto": currency2, "amount": amount}
        
        # Logic: Don't send 'via' if it's the same as source/dest or None
        if via and via != currency1 and via != currency2:
            params["via"] = via        
            
        try:
            # Directly call the slickrpc proxy method
            result = self.rpc_connection.estimateconversion(params)
            return result
        except Exception as e:
            # Capture RPC errors (like "insufficient funds" or "path not found")
            logger.error(f"RPC Error in estimate_conversion: {e}")
            raise Exception(f"RPC Error: {e}")
