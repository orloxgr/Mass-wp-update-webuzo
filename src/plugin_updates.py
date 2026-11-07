"""Attribute each failed update to its plugin before considering a manual ZIP."""
import json
import base64
import re

import catalog
import host


def detection(error,external=False,empty_package=False):
    # Names, URLs and an isolated HTTP 403 are not proof of a premium license.
    text=re.sub(r'https?://\S+','',str(error).lower())
    license_failure=(r'(?:licen[cs]e(?:\s+key)?|subscription)[^\n.!?]{0,70}'
                     r'\b(?:expired|invalid|inactive|required|missing|not active|not activated|not valid|activate|renew|unavailable)\b'
                     r'|\b(?:expired|invalid|inactive|missing|activate|renew|require[sd]?)\b[^\n.!?]{0,50}'
                     r'(?:licen[cs]e(?:\s+key)?|subscription)')
    if re.search(license_failure,text):return 'license_required'
    if external and re.search(r'\b(?:unauthorized|unauthorised|401)\b',text):return 'download_authorization'
    if external and empty_package and re.search(r'\b(?:update package not available|no package available|package is not available)\b',text):
        return 'package_unavailable'
    return None


def update_context(site,plugin,scratch):
    # Emit only booleans. Vendor download URLs can contain license credentials.
    code='''require_once ABSPATH.'wp-admin/includes/plugin.php';
$plugins=get_plugins();$file=FILE_LITERAL;$header=$plugins[$file]??array();
$updates=get_site_transient('update_plugins');$offer=$updates->response[$file]??null;
$external=false;foreach(array($header['UpdateURI']??'', $offer->package??'', $offer->id??'') as $url){
 $host=strtolower((string)wp_parse_url($url,PHP_URL_HOST));
 if($host && $host!=='wordpress.org' && substr($host,-14)!=='.wordpress.org' && $host!=='w.org' && substr($host,-6)!=='.w.org'){$external=true;}
}
echo wp_json_encode(array('external'=>$external,'empty_package'=>is_object($offer)&&empty($offer->package)));'''.replace('FILE_LITERAL',json.dumps(plugin['file']))
    result=json.loads(host.wp(site,['eval',code],scratch))
    if not isinstance(result,dict) or set(result)!={'external','empty_package'} or any(type(v) is not bool for v in result.values()):
        raise ValueError('Invalid plugin update context')
    return result


def update(site,inventory,packages,scratch,progress,result):
    available=json.loads(host.wp(site,['plugin','list','--update=available','--fields=name,update_version','--format=json'],scratch))
    installed={catalog.plugin_slug(plugin['file']):plugin for plugin in inventory['plugins']}
    if not isinstance(available,list):raise ValueError('Invalid plugin update list')
    plan=[];seen=set()
    for offer in available:
        if (not isinstance(offer,dict) or not isinstance(offer.get('name'),str) or offer['name'] not in installed
                or not isinstance(offer.get('update_version'),str) or len(offer['update_version'])>128):
            raise ValueError('Plugin update identity does not match the site inventory')
        slug=offer['name']
        if slug not in packages and slug not in seen:plan.append(offer);seen.add(slug)
    failures=[]
    for offer in plan:
        slug=offer['name'];plugin=installed[slug]
        progress('Updating '+plugin['name']+' on '+site['domain'])
        try:
            # JSON mode suppresses the upgrader warnings that explain license
            # failures. One plugin per command keeps those warnings attributable.
            if slug.startswith('-'):
                encoded=base64.b64encode(slug.encode()).decode()
                code='WP_CLI::get_runner()->run_command(array("plugin","update",base64_decode("'+encoded+'")),array());'
                output=host.wp(site,['eval',code],scratch)
            else:output=host.wp(site,['plugin','update',slug],scratch)
            if re.search(r'(?m)^'+re.escape(slug)+r'\t[^\n]*\tSkipped\s*$',output):
                result.setdefault('skipped',[]).append(plugin['name']+': skipped by the WordPress updater')
            else:result['actions'].append('Plugin: '+plugin['name'])
        except RuntimeError as error:
            detail=str(error)
            reason=detection(detail)
            if not reason:
                try:reason=detection(detail,**update_context(site,plugin,scratch))
                except Exception:pass  # Keep the original update failure when metadata cannot be read.
            if reason:
                try:
                    if catalog.queue_pending(plugin,site,reason,offer['update_version']):
                        result.setdefault('pending_premium',[]).append(plugin['name'])
                except Exception as pending_error:
                    failures.append(plugin['name']+': Could not add waiting library entry: '+str(pending_error))
            # Never persist signed download links or license values from URLs.
            detail=re.sub(r'https?://\S+','[download URL omitted]',detail).strip()
            failures.append(plugin['name']+': '+detail)
    if failures:raise RuntimeError('\n'.join(failures))
