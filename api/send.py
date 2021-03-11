import urlparse
import os, sys
from blockchain_utils import *
from common import *
from debug import *
from decimal import Decimal
import random
import config

try:
  TESTNET = (config.TESTNET == 1)
except:
  TESTNET = False



def send_form_response(response_dict):
    expected_fields=['from_address', 'to_address', 'amount', 'fee']
    # if marker is True, send dust to marker (for payments of sells)
    for field in expected_fields:
        if not response_dict.has_key(field):
            info('No field '+field+' in response dict '+str(response_dict))
            return (None, 'No field '+field+' in response dict '+str(response_dict))
        if len(response_dict[field]) != 1:
            info('Multiple values for field '+field)
            return (None, 'Multiple values for field '+field)

    if "." in response_dict['amount']:
      return (None, "Invalid Format. Amount should be specified in satoshis with no decimal point")

    if 'currency' in response_dict and (response_dict['currency'] not in ['BTC','btc',0,'0']):
        return (None, "Endpoint does not support that currency")

    if TESTNET or ('testnet' in response_dict and ( response_dict['testnet'][0] in ['true', 'True'] )):
        testnet =True
        magicbyte = 111
        exodus_address='mpexoDuSkGGqvqrkrjiFng38QPkJQVFyqv'
    else:
        testnet = False
        magicbyte = 0
        exodus_address='1EXoDusjGwvnjZUyKkxZ4UHEf77z6A5S4P'

    if response_dict.has_key( 'pubKey' ) and is_pubkey_valid( response_dict['pubKey'][0]):
        pubkey = response_dict['pubKey'][0]
        response_status='OK'
    else:
        response_status='invalid pubkey'
        pubkey=None

    print_debug(response_dict,4)

    from_addr=response_dict['from_address'][0]
    #if not is_valid_bitcoin_address_or_pubkey(from_addr):
    #    return (None, 'From address is neither bitcoin address nor pubkey')
    to_addr=response_dict['to_address'][0]
    #if not is_valid_bitcoin_address(to_addr):
    #    return (None, 'To address is not a bitcoin address')
    amount=response_dict['amount'][0]
    if float(amount)<0 or float( from_satoshi(amount))>max_currency_value:
        return (None, 'Invalid amount: ' + str( from_satoshi( amount )) + ', max: ' + str( max_currency_value ))
    btc_fee=response_dict['fee'][0]
    if float(btc_fee)<0 or float( btc_fee )>max_currency_value:
        return (None, 'Invalid fee: ' + str( btc_fee ) + ', max: ' + str( max_currency_value ))

    marker_addr=None
    try:
        marker=response_dict['marker'][0]
        if marker.lower()=='true':
            marker_addr=exodus_address
    except KeyError:
        # if no marker, marker_addr stays None
        pass

    if pubkey == None:
        tx_to_sign_dict={'transaction':'','sourceScript':''}
        l=len(from_addr)
        if l == 66 or l == 130: # probably pubkey
            if is_pubkey_valid(from_addr):
                pubkey=from_addr
                response_status='OK'
            else:
                response_status='invalid pubkey'
        else:
            if not is_valid_bitcoin_address(from_addr):
                response_status='invalid address'
            else:
                from_pubkey=bc_getpubkey(from_addr)
                if not is_pubkey_valid(from_pubkey):
                    response_status='missing pubkey'
                else:
                    pubkey=from_pubkey
                    response_status='OK'

    try:
      if pubkey != None:
          tx_to_sign_dict=prepare_send_tx_for_signing( pubkey, to_addr, marker_addr, amount, to_satoshi(btc_fee), magicbyte)
      else:
          # hack to show error on page
          tx_to_sign_dict['sourceScript']=response_status

      response='{"status":"'+response_status+'", "transaction":"'+tx_to_sign_dict['transaction']+'", "sourceScript":"'+tx_to_sign_dict['sourceScript']+'"}'
      print_debug(("Sending unsigned tx to user for signing", response),4)
      return (response, None)
    except Exception as e:
      print_debug(("error creating unsigned tx", e),2)
      return (None, str(e))


# simple send and bitcoin send (with or without marker)
def prepare_send_tx_for_signing(from_address, to_address, marker_address, amount, btc_fee=500000, magicbyte=0):
    print_debug('*** send.py tx for signing: from_address, to_address, marker_address, amount, btc_fee, magicbyte',4)
    print_debug((from_address, to_address, marker_address, amount, btc_fee, magicbyte),4)

    # consider a more general func that covers also sell offer and sell accept

    # check if address or pubkey was given as from address
    if from_address.startswith('0'): # a pubkey was given
        from_address_pub=from_address
        from_address=pybitcointools.pubkey_to_address(from_address,magicbyte)
    else: # address was given
        from_address_pub=addrPub=bc_getpubkey(from_address)
        from_address_pub=from_address_pub.strip()

    # set change address to from address
    change_address_pub=from_address_pub
    changeAddress=from_address

    satoshi_amount=int( amount )
    fee=int( btc_fee )

    # normal bitcoin send
    required_value=satoshi_amount
    # if marker is needed, allocate dust for the marker
    if marker_address != None:
        required_value+=1*dust_limit

    #------------------------------------------- New utxo calls
    fee_total_satoshi=required_value+fee
    dirty_txes = bc_getutxo( from_address, fee_total_satoshi )

    if (dirty_txes['error'][:3]=='Con'):
        raise Exception({ "status": "NOT OK", "error": "Could not get list of unspent txs. Response Code: " + str(dirty_txes['code']) })

    if (dirty_txes['error'][:3]=='Low'):
        raise Exception({ "status": "NOT OK", "error": "Not enough funds, try again. Needed: " + str(fee_total_satoshi/Decimal(1e8)) + " but Have: " + str(dirty_txes['avail']/Decimal(1e8))  })

    inputs_total_value = dirty_txes['avail']
    inputs = dirty_txes['utxos']

    inputs_outputs='/dev/stdout'
    ins=[]
    outs=[]
    for i in inputs:
        inhash=str(i[0])+':'+str(i[1])
        inputs_outputs+=' -i '+inhash
        ins.append(inhash)


    # calculate change
    change_value=inputs_total_value-required_value-fee
    if change_value < 0:
        info('Error not enough BTC to generate tx - negative change')
        raise Exception('This address must have enough BTC for miner fees and protocol transaction fees')

    # create a normal bitcoin transaction (not mastercoin)
    # dust to marker if required
    # amount to to_address
    # change to change
    if marker_address != None:
        inputs_outputs+=' -o '+marker_address+':'+str(dust_limit)
        outs.append(marker_address+':'+str(dust_limit))
    inputs_outputs+=' -o '+to_address+':'+str(satoshi_amount)
    outs.append(to_address+':'+str(satoshi_amount))

    if change_value >= dust_limit:
        inputs_outputs+=' -o '+changeAddress+':'+str(change_value)
        outs.append(changeAddress+':'+str(change_value))
    else:
        # under dust limit leave all remaining as fees
        pass

    #tx=mktx(inputs_outputs)
    tx=pybitcointools.mktx(ins,outs)
    info('inputs_outputs are '+str(ins)+' '+str(outs))
    #info('inputs_outputs are '+inputs_outputs)
    info('parsed tx is '+str(pybitcointools.deserialize(tx)))

    hash160=bc_address_to_hash_160(from_address).encode('hex_codec')
    prevout_script='OP_DUP OP_HASH160 ' + hash160 + ' OP_EQUALVERIFY OP_CHECKSIG'

    # tx, inputs
    return_dict={'transaction':tx, 'sourceScript':prevout_script}
    return return_dict

def send_handler(environ, start_response):
    return general_handler(environ, start_response, send_form_response)

