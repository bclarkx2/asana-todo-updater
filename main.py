"""Update Asana tasks with urgency based on custom fields."""

import argparse
import asana
import datetime
import numpy
import os
import sys

OPT_FIELDS = 'name,completed,due_on,start_on,custom_fields'


def main():
    """Update Asana tasks with urgency based on custom fields."""
    args = parse_args(sys.argv[1:])

    client = asana.Client.access_token(args.personal_access_token)
    client.options['client_name'] = 'asana-todo-updater'
    client.headers['Asana-Disable'] = 'new_goal_memberships,new_user_task_lists'

    args.func(args, client)


def order(args, client):
    """Order tasks in a section of a project."""
    # If we have a section to fix ordering on, do that first
    if args.section_gid is not None:
        # First, fetch all tasks from the section
        try:
            tasks = client.tasks.get_tasks_for_section(
                args.section_gid,
                {'opt_fields': OPT_FIELDS}
            )
        except asana.error.AsanaError as e:
            print(f"Asana error getting sections: {e}")
            return
        except Exception as e:
            print(f"Unknown error getting sections: {e}")
            return

        # Now, sort the tasks by the order field, with unordered tasks at the
        # end
        toSort = []
        for task in tasks:
            # First parse out all the fields we'll need
            fields = {}
            for field in task['custom_fields']:
                fields[field['gid']] = field

            toSort.append({
                'name': task['name'],
                'gid': task['gid'],
                'order': parse_number_field(fields, args.order_field_gid)
            })
        didSort = sorted(toSort, key=lambda x: (x['order'] is None, x['order']))

        # Finally, re-order the tasks one by one.
        order = 0
        for task in didSort:
            order += 10
            try:
                if task['order'] is not None:
                    print(f"Setting order: {order} => {task['name']}")
                    client.tasks.update_task(task['gid'], {
                        'custom_fields': {
                            args.order_field_gid: order
                        }
                    })
                else:
                    print(f"Not ordering: {task['name']}")
            except asana.error.AsanaError as e:
                print(f"{task['name']} => Asana error ordering task: {e}")
                return
            except Exception as e:
                print(f"{task['name']} => Unknown error ordering task: {e}")
                return
        return


def urgency(args, client):
    """Update urgency of tasks in a project."""
    # Retrieve all incomplete tasks in the specific project
    try:
        tasks = client.tasks.get_tasks({
            'project': args.project_gid,
            'completed_since': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'opt_fields': OPT_FIELDS
        })
    except asana.error.AsanaError as e:
        print(f"Asana error getting project tasks: {e}")
        return
    except Exception as e:
        print(f"Unknown error getting project tasks: {e}")
        return

    _assign_urgency(args, client, tasks)


def task(args, client):
    """Update urgency of specific tasks."""
    try:
        tasks = [client.tasks.get_task(gid, {
            'opt_fields': OPT_FIELDS
        }) for gid in args.task_gids]
    except asana.error.AsanaError as e:
        print(f"Asana error getting specific tasks: {e}")
        return
    except Exception as e:
        print(f"Unknown error getting specific tasks: {e}")
        return

    _assign_urgency(args, client, tasks)


def _assign_urgency(args, client, tasks):
    """Assign urgency to tasks based on custom fields."""
    # Update urgency for each task
    for task in tasks:

        # First parse out all the fields we'll need
        fields = {}
        for field in task['custom_fields']:
            fields[field['gid']] = field

        name = task['name']
        completed = parse_bool_field(task, 'completed')
        due_on = parse_date_field(task, 'due_on')
        open_date = parse_date_custom_field(fields, args.open_date_field_gid)
        impact = parse_enum_custom_field(fields, args.impact_field_gid)
        size = parse_enum_custom_field(fields, args.size_field_gid)

        # Can skip tasks that are already completed
        if completed:
            continue

        # Can skip tasks that aren't small enough to work on specifically
        if size == 'Holder':
            continue

        # Compute desired urgency value
        urgency = compute_urgency(due_on, open_date, impact)

        # Update only the urgency field on the source task
        try:
            client.tasks.update_task(task['gid'], {
                'custom_fields': {
                    args.urgency_field_gid: urgency
                }
            })
        except asana.error.AsanaError as e:
            print(f"{name} => Asana error updating task: {e}")
        except Exception as e:
            print(f"{name} => Unknown error updating task: {e}")

        print(f"{name} => {urgency}")


def parse_enum_custom_field(fields, field_gid):
    """Parse an enum custom field from the fields dictionary."""
    try:
        return fields[field_gid]['enum_value']['name']
    except TypeError:
        return None
    except KeyError:
        return None


def parse_date_custom_field(fields, field_gid):
    """Parse a date custom field from the fields dictionary."""
    datestr = ''
    try:
        datestr = fields[field_gid]['date_value']['date']
    except TypeError:
        return None
    except KeyError:
        return None

    return parse_date(datestr)


def parse_bool_field(fields, field_name):
    """Parse a boolean field from the fields dictionary."""
    fieldval = None
    try:
        fieldval = fields[field_name]
    except KeyError:
        return False

    try:
        return bool(fieldval)
    except ValueError:
        return False


def parse_number_field(fields, field_gid):
    """Parse a number field from the fields dictionary."""
    try:
        return fields[field_gid]['number_value']
    except KeyError:
        return None


def parse_date_field(fields, field_name):
    """Parse a date field from the fields dictionary."""
    datestr = ''
    try:
        datestr = fields[field_name]
    except KeyError:
        return None

    return parse_date(datestr)


def parse_date(datestr):
    """Parse a date string into a date object."""
    if datestr is None:
        return None

    try:
        return datetime.date.fromisoformat(datestr)
    except ValueError:
        return None


def compute_urgency(due_on, open_date, impact):
    """Compute urgency value based on due date, open date, and impact."""
    urgency = 0

    match impact:
        case 'Very High':
            urgency += 20
        case 'High':
            urgency += 10
        case 'Medium':
            urgency += 2
        case 'Low':
            urgency += 1
        case None:
            return 0

    if due_on is not None:
        remaining_days = numpy.busday_count(datetime.date.today(), due_on)

        if remaining_days < 0:
            urgency *= 5
        elif remaining_days == 0:
            urgency *= 3
        elif remaining_days == 1:
            urgency *= 2
        elif remaining_days == 2:
            urgency *= 1.5
        elif remaining_days == 3:
            urgency *= 1.2
        elif remaining_days == 4:
            urgency *= 1.1
        elif remaining_days >= 10:
            urgency *= 0.8
        elif remaining_days == 20:
            urgency *= 0.5

    if open_date is not None:
        open_days = numpy.busday_count(open_date, datetime.date.today())
        open_weeks = open_days // 5
        if open_weeks > 0 and open_weeks < 10:
            urgency += open_weeks * 0.5
        else:
            urgency += open_weeks * 0.1

    return urgency


def parse_args(argv=None):
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Find unexpected connect page data',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        '--personal-access-token',
        default=os.environ.get('PERSONAL_ACCESS_TOKEN', ''),
        help='filepath to pull SF accounts from')

    parser.add_argument(
        '--open-date-field-gid',
        default=os.environ.get('OPEN_DATE_FIELD_GID', '1205994998304482'),
        help='gid of custom field holding open date')

    parser.add_argument(
        '--impact-field-gid',
        default=os.environ.get('IMPACT_FIELD_GID', '1205872827118668'),
        help='gid of custom field holding impact')

    parser.add_argument(
        '--size-field-gid',
        default=os.environ.get('SIZE_FIELD_GID', '1205994859505931'),
        help='gid of custom field holding size')

    parser.add_argument(
        '--urgency-field-gid',
        default=os.environ.get('URGENCY_FIELD_GID', '1205994998304510'),
        help='gid of custom field holding urgency')

    parser.add_argument(
        '--order-field-gid',
        default=os.environ.get('ORDER_FIELD_GID', '1206071193914820'),
        help='gid of custom field holding order')

    # Commands
    subparsers = parser.add_subparsers(
        title='commands',
        description='valid commands',
        help='additional help',
        dest='command'
    )

    # # Order
    parser_order = subparsers.add_parser(
        'order',
        help='Order tasks in a section'
    )
    parser_order.add_argument(
        '--section-gid',
        default=os.environ.get('FIX_SECTION_ORDERING', '1206005792093857'),
        help='re-order tasks in section of project')
    parser_order.set_defaults(func=order)

    # # Urgency
    parser_urgency = subparsers.add_parser(
        'urgency',
        help='Update urgency of tasks'
    )
    parser_urgency.add_argument(
        '--project-gid',
        default=os.environ.get('PROJECT_GID', '1205994859505918'),
        help='gid for project to update')
    parser_urgency.set_defaults(func=urgency)

    # # Task
    parser_task = subparsers.add_parser(
        'task',
        help='Update urgency of specific tasks'
    )
    parser_task.add_argument(
        'task_gids',
        nargs='+',
        type=str,
        help='gid for specific tasks to update')
    parser_task.set_defaults(func=task)

    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
